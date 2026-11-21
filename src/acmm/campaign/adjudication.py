from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping
import uuid

import yaml
from jsonschema import Draft202012Validator

from acmm.campaign.execution import inspect_campaign_status
from acmm.campaign.merge import (
    accepted_campaign_observation,
    collect_accepted_campaign_observations,
)
from acmm.campaign.model import campaign_artifact, campaign_error
from acmm.evidence.interpretation import interpret_scenario_evidence
from acmm.experiment_package.adjudication import load_staged_experiment_adjudicator
from acmm.experiment_package.model import experiment_package
from acmm.experiment_package.staging import (
    open_staged_experiment_package,
    stage_experiment_package,
)
from acmm.record.loader import load_run_record
from acmm.runtime import execution as runtime_execution
from acmm.runtime.interfaces import captured_evidence
from acmm.runtime.registry import execution_registry


CAMPAIGN_ADJUDICATION_SCHEMA_VERSION = "0.2"
_CAMPAIGN_ADJUDICATION_SCHEMA_FILES = {
    "0.1": "campaign-adjudication-v0.1.schema.json",
    "0.2": "campaign-adjudication-v0.2.schema.json",
}


@dataclass(frozen=True)
class campaign_adjudication_artifact:
    adjudication_id: str
    path: Path
    manifest_path: Path
    manifest: Mapping[str, Any]


def create_campaign_adjudication(
    artifact: campaign_artifact,
    package: experiment_package,
    *,
    adjudication_id: str | None = None,
    now: Callable[[], datetime] | None = None,
    progress: Callable[[str], None] | None = None,
) -> campaign_adjudication_artifact:
    status = inspect_campaign_status(artifact)
    if status.runs.active or status.executions.active:
        raise campaign_error(
            "Campaign adjudication requires no active campaign executions."
        )
    if status.runs.observed != status.runs.total or status.runs.pending:
        raise campaign_error(
            "Complete Campaign adjudication requires every logical run to be observed: "
            f"observed={status.runs.observed}, pending={status.runs.pending}, "
            f"total={status.runs.total}."
        )
    if package.experiment_id != artifact.experiment_id:
        raise campaign_error(
            f"Adjudicator package experiment '{package.experiment_id}' does not match "
            f"Campaign experiment '{artifact.experiment_id}'."
        )
    frozen_contract_sha = artifact.manifest["package"]["contract_sha256"]
    if package.contract_sha256 != frozen_contract_sha:
        raise campaign_error(
            "Adjudicator package Contract SHA-256 must exactly match the frozen Campaign "
            f"Contract ({frozen_contract_sha}); observed {package.contract_sha256}."
        )
    if package.adjudication is None:
        raise campaign_error(
            f"Adjudicator package '{package.package_sha256}' does not declare adjudication code."
        )

    loaded = load_staged_experiment_adjudicator(package)
    accepted = collect_accepted_campaign_observations(artifact)
    if set(accepted) != set(status.observed_run_ids):
        raise campaign_error(
            "Campaign adjudication accepted-observation set does not match Campaign status."
        )

    contract = _load_yaml_mapping(
        artifact.path / artifact.manifest["campaign"]["contract_artifact"],
        "frozen Campaign Contract",
    )
    subjects = {item["id"]: item for item in contract.get("subjects", ()) if isinstance(item, dict)}
    scenarios = {item["id"]: item for item in contract.get("scenarios", ()) if isinstance(item, dict)}

    _validate_adjudicator_evaluators(contract, loaded.adjudicator.evaluators)
    replay_registry = execution_registry(
        evaluators=dict(loaded.adjudicator.evaluators),
    )

    realized_id = adjudication_id if adjudication_id is not None else str(uuid.uuid4())
    _validate_identifier("adjudication ID", realized_id)
    clock = now if now is not None else _utc_now
    created_at = clock()
    if created_at.tzinfo is None:
        raise campaign_error("Campaign adjudication timestamp must be timezone-aware.")

    root = artifact.path / "adjudications"
    final_dir = root / f"adjudication_{realized_id}"
    if final_dir.exists():
        raise campaign_error(f"Campaign adjudication directory already exists: '{final_dir}'.")
    root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".adjudication_{realized_id}.tmp-", dir=root))

    try:
        if progress is not None:
            progress("Adjudication: snapshotting adjudicator package...")
        staged_snapshot = stage_experiment_package(
            package,
            temp_dir / "package_store",
        )
        if staged_snapshot.package_sha256 != package.package_sha256:
            raise campaign_error(
                "Campaign adjudication package snapshot SHA-256 does not match the source package."
            )
        (temp_dir / "runs").mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        changed_runs = 0
        changed_judgments = 0
        logical_order = {
            item["run_id"]: position
            for position, item in enumerate(artifact.manifest["runs"])
        }
        ordered_run_ids = sorted(accepted, key=logical_order.__getitem__)
        total_runs = len(ordered_run_ids)
        for position, logical_run_id in enumerate(ordered_run_ids, start=1):
            observation = accepted[logical_run_id]
            record = load_run_record(observation.source_run_record_path)
            subject = subjects.get(observation.subject_id)
            scenario = scenarios.get(observation.scenario_id)
            if not isinstance(subject, dict) or not isinstance(scenario, dict):
                raise campaign_error(
                    f"Frozen Contract is missing subject/scenario for logical run '{logical_run_id}'."
                )

            evidence = _load_evidence(observation, record)
            interpreted = interpret_scenario_evidence(scenario, evidence)
            revised = runtime_execution._evaluate_opportunities(
                contract,
                subject,
                scenario,
                evidence,
                interpreted,
                replay_registry,
            )
            original = record.get("judgments")
            if not isinstance(original, list):
                raise campaign_error(
                    f"Accepted Run Record '{observation.source_run_record_path}' has invalid judgments."
                )
            changes = _judgment_changes(original, revised)
            if changes:
                changed_runs += 1
                changed_judgments += len(changes)

            run_name = f"run_{observation.run_index:06d}_{logical_run_id}.json"
            run_path = temp_dir / "runs" / run_name
            run_payload = {
                "schema_version": "0.1",
                "logical_run_id": logical_run_id,
                "run_index": observation.run_index,
                "subject_id": observation.subject_id,
                "scenario_id": observation.scenario_id,
                "repetition": observation.repetition,
                "source_execution_id": observation.source_execution_id,
                "source_run_id": observation.source_run_id,
                "source_run_record_sha256": _sha256_file(observation.source_run_record_path),
                "original_judgments": original,
                "revised_judgments": revised,
                "changes": changes,
            }
            _write_json(run_path, run_payload)
            entries.append(
                {
                    "logical_run_id": logical_run_id,
                    "run_index": observation.run_index,
                    "subject_id": observation.subject_id,
                    "scenario_id": observation.scenario_id,
                    "repetition": observation.repetition,
                    "source_execution_id": observation.source_execution_id,
                    "source_run_id": observation.source_run_id,
                    "source_run_record": _relative_to_campaign(
                        artifact,
                        observation.source_run_record_path,
                    ),
                    "source_run_record_sha256": _sha256_file(
                        observation.source_run_record_path
                    ),
                    "evidence_sha256": {
                        item["id"]: item["sha256"]
                        for item in record.get("evidence", ())
                        if isinstance(item, Mapping)
                        and isinstance(item.get("id"), str)
                        and isinstance(item.get("sha256"), str)
                    },
                    "adjudication_artifact": run_path.relative_to(temp_dir).as_posix(),
                    "adjudication_artifact_sha256": _sha256_file(run_path),
                    "changed": bool(changes),
                    "changed_opportunities": [item["opportunity_id"] for item in changes],
                }
            )
            if progress is not None and _progress_checkpoint(position, total_runs):
                progress(f"Adjudication: {position}/{total_runs} observations processed...")

        manifest = {
            "schema_version": CAMPAIGN_ADJUDICATION_SCHEMA_VERSION,
            "adjudication": {
                "id": realized_id,
                "campaign_id": artifact.campaign_id,
                "experiment_id": artifact.experiment_id,
                "created_at": _format_timestamp(created_at),
                "campaign_manifest_sha256": _sha256_file(artifact.manifest_path),
                "design_digest": artifact.manifest["campaign"]["design_digest"],
                "contract_sha256": frozen_contract_sha,
                "acquisition_package_sha256": artifact.manifest["package"]["sha256"],
                "adjudicator_package_sha256": package.package_sha256,
                "adjudicator_snapshot_artifact": "package_store",
                "adjudication_component_sha256": package.adjudication.sha256,
                "adjudication_api_version": package.adjudication.api_version,
            },
            "population": {
                "total": status.runs.total,
                "adjudicated": len(entries),
                "changed_runs": changed_runs,
                "changed_judgments": changed_judgments,
            },
            "runs": entries,
        }
        validate_campaign_adjudication_manifest(manifest)
        _write_json(temp_dir / "campaign_adjudication.json", manifest)
        os.rename(temp_dir, final_dir)
    except BaseException:
        import shutil
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise

    return campaign_adjudication_artifact(
        adjudication_id=realized_id,
        path=final_dir,
        manifest_path=final_dir / "campaign_adjudication.json",
        manifest=manifest,
    )


def validate_campaign_adjudication_manifest(manifest: Mapping[str, Any]) -> None:
    version = manifest.get("schema_version")
    schema_file = _CAMPAIGN_ADJUDICATION_SCHEMA_FILES.get(str(version))
    if schema_file is None:
        raise campaign_error(
            f"Unsupported Campaign Adjudication schema_version '{version}'."
        )
    schema_path = Path(__file__).with_name("schemas") / schema_file
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "$"
        raise campaign_error(
            f"Campaign adjudication validation failed at '{location}': {first.message}"
        )


def load_campaign_adjudication(
    artifact: campaign_artifact,
    adjudication_id: str,
) -> campaign_adjudication_artifact:
    _validate_identifier("adjudication ID", adjudication_id)
    path = artifact.path / "adjudications" / f"adjudication_{adjudication_id}"
    manifest_path = path / "campaign_adjudication.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise campaign_error(f"Campaign adjudication does not exist safely: '{manifest_path}'.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise campaign_error(f"Campaign adjudication manifest '{manifest_path}' is malformed.")
    validate_campaign_adjudication_manifest(manifest)
    identity = manifest["adjudication"]
    if identity["campaign_id"] != artifact.campaign_id:
        raise campaign_error("Campaign adjudication belongs to a different Campaign.")
    if identity["contract_sha256"] != artifact.manifest["package"]["contract_sha256"]:
        raise campaign_error("Campaign adjudication Contract does not match frozen Campaign.")
    if manifest.get("schema_version") == "0.2":
        snapshot_artifact = identity.get("adjudicator_snapshot_artifact")
        if not isinstance(snapshot_artifact, str) or not snapshot_artifact:
            raise campaign_error("Campaign adjudication package snapshot metadata is invalid.")
        snapshot_root = _safe_adjudication_artifact(path, snapshot_artifact)
        try:
            snapshotted = open_staged_experiment_package(
                snapshot_root,
                artifact.experiment_id,
                identity["adjudicator_package_sha256"],
            )
        except (OSError, ValueError) as exc:
            raise campaign_error(
                f"Campaign adjudication package snapshot is invalid: {exc}"
            ) from exc
        if snapshotted.contract_sha256 != identity["contract_sha256"]:
            raise campaign_error(
                "Campaign adjudication package snapshot Contract does not match adjudication provenance."
            )
    return campaign_adjudication_artifact(
        adjudication_id=adjudication_id,
        path=path,
        manifest_path=manifest_path,
        manifest=manifest,
    )


def _progress_checkpoint(position: int, total: int) -> bool:
    if total <= 0 or position >= total:
        return True
    interval = 50 if total >= 100 else 25 if total >= 50 else 10 if total >= 20 else 0
    return interval > 0 and position % interval == 0


def _safe_adjudication_artifact(adjudication_dir: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise campaign_error(f"Unsafe Campaign adjudication artifact path: '{relative}'.")
    candidate = adjudication_dir / path
    if candidate.is_symlink():
        raise campaign_error(
            f"Campaign adjudication artifact must not be a symbolic link: '{relative}'."
        )
    resolved = candidate.resolve()
    try:
        resolved.relative_to(adjudication_dir.resolve())
    except ValueError as exc:
        raise campaign_error(
            f"Campaign adjudication artifact escapes its directory: '{relative}'."
        ) from exc
    return resolved


def _load_evidence(
    observation: accepted_campaign_observation,
    record: Mapping[str, Any],
) -> tuple[captured_evidence, ...]:
    realized: list[captured_evidence] = []
    for item in record.get("evidence", ()):
        if not isinstance(item, Mapping):
            continue
        evidence_id = item.get("id")
        channel = item.get("channel")
        artifact = item.get("artifact")
        expected = item.get("sha256")
        if not all(isinstance(value, str) and value for value in (evidence_id, channel, artifact, expected)):
            raise campaign_error(
                f"Run Record '{observation.source_run_record_path}' has malformed evidence metadata."
            )
        path = _safe_result_artifact(observation.source_result_dir, artifact)
        content = path.read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected:
            raise campaign_error(f"Evidence SHA-256 mismatch for '{path}'.")
        realized.append(
            captured_evidence(
                id=evidence_id,
                channel=channel,
                artifact=artifact,
                content=content,
            )
        )
    return tuple(realized)


def _validate_adjudicator_evaluators(
    contract: Mapping[str, Any],
    evaluators: Mapping[str, Any],
) -> None:
    checks = {
        item["id"]: item
        for item in contract.get("checks", ())
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    required: set[str] = set()
    for scenario in contract.get("scenarios", ()):
        if not isinstance(scenario, Mapping):
            continue
        for opportunity in scenario.get("opportunities", ()):
            if not isinstance(opportunity, Mapping):
                continue
            check = checks.get(opportunity.get("check_id"))
            if isinstance(check, Mapping) and isinstance(check.get("evaluator"), str):
                required.add(check["evaluator"])
    missing = sorted(required - set(evaluators))
    if missing:
        raise campaign_error(
            "Adjudicator does not provide evaluator(s): " + ", ".join(missing) + "."
        )


def _judgment_changes(
    original: list[Any],
    revised: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    before = {
        item.get("opportunity_id"): item
        for item in original
        if isinstance(item, Mapping) and isinstance(item.get("opportunity_id"), str)
    }
    after = {item["opportunity_id"]: item for item in revised}
    changes: list[dict[str, Any]] = []
    for opportunity_id in sorted(set(before) | set(after)):
        old = before.get(opportunity_id)
        new = after.get(opportunity_id)
        if old != new:
            changes.append(
                {
                    "opportunity_id": opportunity_id,
                    "before": old,
                    "after": new,
                }
            )
    return changes


def _safe_result_artifact(result_dir: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise campaign_error(f"Unsafe evidence artifact path: '{relative}'.")
    current = result_dir
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise campaign_error(f"Evidence artifact path traverses symbolic link: '{relative}'.")
    resolved = current.resolve()
    try:
        resolved.relative_to(result_dir.resolve())
    except ValueError as exc:
        raise campaign_error(f"Evidence artifact escapes result directory: '{relative}'.") from exc
    if not resolved.is_file():
        raise campaign_error(f"Evidence artifact does not exist: '{resolved}'.")
    return resolved


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise campaign_error(f"Could not load {label} '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise campaign_error(f"{label} '{path}' must contain a mapping.")
    return value


def _relative_to_campaign(artifact: campaign_artifact, path: Path) -> str:
    try:
        return path.resolve().relative_to(artifact.path.resolve()).as_posix()
    except ValueError as exc:
        raise campaign_error(f"Path '{path}' escapes Campaign root.") from exc


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_identifier(label: str, value: str) -> None:
    if not isinstance(value, str) or not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in value):
        raise campaign_error(f"{label} is invalid: '{value}'.")


__all__ = [
    "CAMPAIGN_ADJUDICATION_SCHEMA_VERSION",
    "campaign_adjudication_artifact",
    "create_campaign_adjudication",
    "load_campaign_adjudication",
    "validate_campaign_adjudication_manifest",
]
