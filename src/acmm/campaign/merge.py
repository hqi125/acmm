from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Mapping
import uuid

import yaml
from jsonschema import Draft202012Validator

from acmm.campaign.execution import (
    campaign_execution_outcomes,
    inspect_campaign_status,
    read_campaign_events,
)
from acmm.campaign.model import campaign_artifact, campaign_error
from acmm.record.loader import load_run_record, run_record_load_error
from acmm.record.validator import (
    default_run_record_schema_path,
    load_run_record_schema,
    validate_run_record,
)


CAMPAIGN_MERGE_SCHEMA_VERSION = "0.2"
_CAMPAIGN_MERGE_SCHEMA_FILES = {
    "0.1": "campaign-merge-v0.1.schema.json",
    "0.2": "campaign-merge-v0.2.schema.json",
}


@dataclass(frozen=True)
class campaign_merge_artifact:
    merge_id: str
    path: Path
    manifest_path: Path
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class _accepted_observation:
    logical_run_id: str
    run_index: int
    subject_id: str
    scenario_id: str
    repetition: int
    source_execution_id: str
    source_execution_status: str
    source_attempt: int
    source_run_id: str
    source_session_dir: Path
    source_result_dir: Path
    source_campaign_execution_path: Path
    source_run_record_path: Path


def create_campaign_merge(
    artifact: campaign_artifact,
    *,
    allow_partial: bool = False,
    merge_id: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> campaign_merge_artifact:
    status = inspect_campaign_status(artifact)
    if status.runs.active or status.executions.active:
        raise campaign_error(
            "Campaign merge requires no active campaign executions. Reconcile or finish "
            "the active execution first."
        )

    if not allow_partial and (
        status.runs.observed != status.runs.total
        or status.runs.pending
    ):
        raise campaign_error(
            "Complete campaign merge requires every logical run to be observed: "
            f"observed={status.runs.observed}, pending={status.runs.pending}, "
            f"total={status.runs.total}."
        )

    accepted = _collect_accepted_observations(artifact)
    expected_observed = set(status.observed_run_ids)
    actual_observed = set(accepted)
    if actual_observed != expected_observed:
        missing = sorted(expected_observed - actual_observed)
        unexpected = sorted(actual_observed - expected_observed)
        details: list[str] = []
        if missing:
            details.append("missing accepted observation(s): " + ", ".join(missing))
        if unexpected:
            details.append("unexpected accepted observation(s): " + ", ".join(unexpected))
        raise campaign_error("Campaign merge observation mismatch: " + "; ".join(details))

    realized_merge_id = merge_id if merge_id is not None else str(uuid.uuid4())
    _validate_identifier("merge ID", realized_merge_id)
    clock = now if now is not None else _utc_now
    created_at = clock()
    if created_at.tzinfo is None:
        raise campaign_error("Campaign merge timestamp must be timezone-aware.")

    merge_root = artifact.path / "merged"
    final_dir = merge_root / f"merge_{realized_merge_id}"
    if final_dir.exists():
        raise campaign_error(f"Campaign merge directory already exists: '{final_dir}'.")
    merge_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=f".merge_{realized_merge_id}.tmp-",
            dir=merge_root,
        )
    )

    try:
        shutil.copy2(artifact.manifest_path, temp_dir / "campaign.json")
        frozen_contract = artifact.path / artifact.manifest["campaign"]["contract_artifact"]
        shutil.copy2(frozen_contract, temp_dir / "contract.yaml")
        (temp_dir / "runs").mkdir(parents=True, exist_ok=True)

        observations: list[dict[str, Any]] = []
        logical_order = {
            item["run_id"]: position
            for position, item in enumerate(artifact.manifest["runs"])
        }
        for logical_run_id in sorted(accepted, key=logical_order.__getitem__):
            observation = accepted[logical_run_id]
            destination_name = f"run_{observation.run_index:06d}_{logical_run_id}"
            destination = temp_dir / "runs" / destination_name
            _copy_result_tree(observation.source_result_dir, destination)
            merged_record = destination / "run_record.yaml"
            observations.append(
                {
                    "logical_run_id": logical_run_id,
                    "run_index": observation.run_index,
                    "subject_id": observation.subject_id,
                    "scenario_id": observation.scenario_id,
                    "repetition": observation.repetition,
                    "source_execution_id": observation.source_execution_id,
                    "source_execution_status": observation.source_execution_status,
                    "source_attempt": observation.source_attempt,
                    "source_run_id": observation.source_run_id,
                    "source_campaign_execution": _relative_to_campaign(
                        artifact,
                        observation.source_campaign_execution_path,
                    ),
                    "source_campaign_execution_sha256": _sha256_file(
                        observation.source_campaign_execution_path
                    ),
                    "source_event_journal_sha256": _sha256_file(
                        observation.source_session_dir / "campaign_events.jsonl"
                    ),
                    "source_result": _relative_to_campaign(
                        artifact,
                        observation.source_result_dir,
                    ),
                    "source_run_record_sha256": _sha256_file(
                        observation.source_run_record_path
                    ),
                    "merged_result": destination.relative_to(temp_dir).as_posix(),
                    "merged_run_record_sha256": _sha256_file(merged_record),
                }
            )

        completeness = "complete" if status.runs.observed == status.runs.total else "partial"
        manifest = {
            "schema_version": CAMPAIGN_MERGE_SCHEMA_VERSION,
            "merge": {
                "id": realized_merge_id,
                "campaign_id": artifact.campaign_id,
                "experiment_id": artifact.experiment_id,
                "created_at": _format_timestamp(created_at),
                "completeness": completeness,
                "allow_partial": bool(allow_partial),
                "design_digest": artifact.manifest["campaign"]["design_digest"],
                "campaign_manifest_sha256": _sha256_file(artifact.manifest_path),
                "contract_sha256": artifact.manifest["package"]["contract_sha256"],
                "package_sha256": artifact.manifest["package"]["sha256"],
            },
            "population": {
                "total": status.runs.total,
                "observed": status.runs.observed,
                "pending": status.runs.pending,
            },
            "observations": observations,
        }
        validate_campaign_merge_manifest(manifest)
        _write_json(temp_dir / "campaign_merge.json", manifest)
        os.rename(temp_dir, final_dir)
    except BaseException:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise

    return campaign_merge_artifact(
        merge_id=realized_merge_id,
        path=final_dir,
        manifest_path=final_dir / "campaign_merge.json",
        manifest=manifest,
    )


def validate_campaign_merge_manifest(manifest: Mapping[str, Any]) -> None:
    version = manifest.get("schema_version")
    if version not in _CAMPAIGN_MERGE_SCHEMA_FILES:
        raise campaign_error(f"Unsupported Campaign Merge schema_version '{version}'.")
    schema_path = (
        Path(__file__).with_name("schemas")
        / _CAMPAIGN_MERGE_SCHEMA_FILES[version]
    )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not load Campaign Merge schema '{schema_path}': {exc}"
        ) from exc
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "$"
        raise campaign_error(
            f"Campaign merge validation failed at '{location}': {first.message}"
        )


def _collect_accepted_observations(
    artifact: campaign_artifact,
) -> dict[str, _accepted_observation]:
    contract = _load_yaml_mapping(
        artifact.path / artifact.manifest["campaign"]["contract_artifact"],
        "frozen campaign Contract",
    )
    record_schema = load_run_record_schema(default_run_record_schema_path())
    logical_by_id = {item["run_id"]: item for item in artifact.manifest["runs"]}
    accepted: dict[str, _accepted_observation] = {}
    executions_root = artifact.path / "executions"
    if not executions_root.exists():
        return accepted

    for session_dir in sorted(executions_root.glob("execution_*")):
        if not session_dir.is_dir() or session_dir.is_symlink():
            continue
        campaign_execution_path = session_dir / "campaign_execution.json"
        if not campaign_execution_path.is_file():
            continue
        campaign_execution = _load_json_mapping(
            campaign_execution_path,
            "campaign execution",
        )
        execution = campaign_execution.get("execution")
        outcomes = campaign_execution.get("outcomes")
        if not isinstance(execution, Mapping) or not isinstance(outcomes, Mapping):
            raise campaign_error(
                f"Campaign execution '{campaign_execution_path}' is malformed."
            )
        if execution.get("campaign_id") != artifact.campaign_id:
            raise campaign_error(
                f"Campaign execution '{campaign_execution_path}' references a different campaign."
            )
        if execution.get("experiment_id") != artifact.experiment_id:
            raise campaign_error(
                f"Campaign execution '{campaign_execution_path}' references a different experiment."
            )
        observed_ids, _ = campaign_execution_outcomes(campaign_execution)
        if not observed_ids:
            continue

        events = read_campaign_events(session_dir / "campaign_events.jsonl").events
        slots_by_index: dict[int, Mapping[str, Any]] | None = None
        legacy_name = execution.get("experiment_execution_artifact")
        if not isinstance(legacy_name, str):
            legacy_name = execution.get("experiment_interruption_artifact")
        if not isinstance(legacy_name, str):
            legacy_name = execution.get("experiment_failure_artifact")
        if isinstance(legacy_name, str):
            legacy_path = _safe_session_artifact(session_dir, legacy_name)
            legacy = _load_yaml_mapping(legacy_path, "source execution manifest")
            legacy_execution = legacy.get("execution")
            if not isinstance(legacy_execution, Mapping):
                raise campaign_error(
                    f"Source execution manifest '{legacy_path}' is missing execution metadata."
                )
            if legacy_execution.get("id") != execution.get("id"):
                raise campaign_error(
                    f"Source execution manifest '{legacy_path}' execution ID does not match "
                    "its Campaign Execution record."
                )
            if legacy_execution.get("experiment_id") != artifact.experiment_id:
                raise campaign_error(
                    f"Source execution manifest '{legacy_path}' belongs to a different experiment."
                )
            if (
                legacy_execution.get("contract_sha256")
                != artifact.manifest["package"]["contract_sha256"]
            ):
                raise campaign_error(
                    f"Source execution manifest '{legacy_path}' Contract SHA-256 does not match "
                    "the frozen campaign."
                )
            package_provenance = legacy_execution.get("package")
            if not isinstance(package_provenance, Mapping):
                raise campaign_error(
                    f"Source execution manifest '{legacy_path}' is missing package provenance."
                )
            if package_provenance.get("sha256") != artifact.manifest["package"]["sha256"]:
                raise campaign_error(
                    f"Source execution manifest '{legacy_path}' package SHA-256 does not match "
                    "the frozen campaign."
                )
            slots = legacy.get("slots")
            if not isinstance(slots, list):
                raise campaign_error(
                    f"Source execution manifest '{legacy_path}' does not contain slots."
                )
            slots_by_index = {}
            for slot in slots:
                if not isinstance(slot, Mapping):
                    raise campaign_error(
                        f"Source execution manifest '{legacy_path}' has malformed slots."
                    )
                index = slot.get("run_index")
                if not isinstance(index, int) or isinstance(index, bool):
                    raise campaign_error(
                        f"Source execution manifest '{legacy_path}' has invalid run_index."
                    )
                if index in slots_by_index:
                    raise campaign_error(
                        f"Source execution manifest '{legacy_path}' contains duplicate run index {index}."
                    )
                slots_by_index[index] = slot
        elif execution.get("status") != "interrupted_unclean":
            raise campaign_error(
                f"Campaign execution '{campaign_execution_path}' records observed runs "
                "without an execution/interruption/failure artifact or an unclean-interruption proof path."
            )

        for logical_run_id in observed_ids:
            if logical_run_id not in logical_by_id:
                raise campaign_error(
                    f"Campaign execution '{campaign_execution_path}' records unknown logical run "
                    f"'{logical_run_id}'."
                )
            if logical_run_id in accepted:
                prior = accepted[logical_run_id]
                raise campaign_error(
                    f"Logical run '{logical_run_id}' has multiple accepted observations in "
                    f"executions '{prior.source_execution_id}' and '{execution.get('id')}'."
                )
            logical = logical_by_id[logical_run_id]
            run_index = logical["run_index"]

            selected_run_id: str | None = None
            if slots_by_index is not None:
                slot = slots_by_index.get(run_index)
                if slot is None:
                    raise campaign_error(
                        f"Observed logical run '{logical_run_id}' is absent from its source execution manifest."
                    )
                selected_run_id = slot.get("selected_run_id")
                attempts = slot.get("attempts")
                if not isinstance(selected_run_id, str) or not selected_run_id:
                    raise campaign_error(
                        f"Observed logical run '{logical_run_id}' does not identify an accepted source run."
                    )
                if not isinstance(attempts, list):
                    raise campaign_error(
                        f"Observed logical run '{logical_run_id}' has malformed source attempts."
                    )
                matching_attempts = [
                    item
                    for item in attempts
                    if isinstance(item, Mapping) and item.get("run_id") == selected_run_id
                ]
                if len(matching_attempts) != 1:
                    raise campaign_error(
                        f"Observed logical run '{logical_run_id}' must map to exactly one accepted attempt."
                    )
                attempt = matching_attempts[0]
                attempt_number = attempt.get("attempt")
                result_path = attempt.get("result_path")
            else:
                persisted = [
                    event
                    for event in events
                    if event.get("event") == "attempt_persisted"
                    and event.get("run_index") == run_index
                    and event.get("validity") == "valid"
                ]
                if len(persisted) != 1:
                    raise campaign_error(
                        f"Observed logical run '{logical_run_id}' must map to exactly one durable valid "
                        "attempt_persisted event when its top-level execution manifest is unavailable."
                    )
                attempt_number = persisted[0].get("attempt")
                result_path = persisted[0].get("result_path")

            if (
                not isinstance(attempt_number, int)
                or isinstance(attempt_number, bool)
                or attempt_number < 1
                or not isinstance(result_path, str)
                or not result_path
            ):
                raise campaign_error(
                    f"Observed logical run '{logical_run_id}' has invalid accepted-attempt metadata."
                )

            source_result_dir = (
                _safe_persisted_result_artifact(session_dir, result_path)
                if slots_by_index is None
                else _safe_session_artifact(session_dir, result_path)
            )
            if not source_result_dir.is_dir() or source_result_dir.is_symlink():
                raise campaign_error(
                    f"Accepted result directory does not exist safely: '{source_result_dir}'."
                )
            record_path = source_result_dir / "run_record.yaml"
            try:
                record = load_run_record(record_path)
            except run_record_load_error as exc:
                raise campaign_error(str(exc)) from exc
            validation = validate_run_record(
                record,
                record_schema,
                contract,
                expected_contract_sha256=artifact.manifest["package"]["contract_sha256"],
            )
            if not validation.is_valid:
                issue = validation.errors[0]
                raise campaign_error(
                    f"Accepted Run Record '{record_path}' is invalid at '{issue.path}': "
                    f"[{issue.code}] {issue.message}"
                )
            run = record["run"]
            if selected_run_id is None:
                selected_run_id = run.get("id")
            if (
                run["id"] != selected_run_id
                or run["run_index"] != run_index
                or run["subject_id"] != logical["subject_id"]
                or run["scenario_id"] != logical["scenario_id"]
                or run["repetition"] != logical["repetition"]
            ):
                raise campaign_error(
                    f"Accepted Run Record '{record_path}' does not match logical run "
                    f"'{logical_run_id}'."
                )
            if record["execution"].get("validity") != "valid":
                raise campaign_error(
                    f"Accepted Run Record '{record_path}' is not scientifically valid."
                )

            journal_finalized = _event_exists(
                events,
                "attempt_finalized",
                run_index=run_index,
                attempt=attempt_number,
            )
            artifact_finalized = _successful_environment_finalization(source_result_dir)
            if not journal_finalized and not artifact_finalized:
                raise campaign_error(
                    f"Observed logical run '{logical_run_id}' lacks durable finalization evidence."
                )

            _validate_record_artifacts(source_result_dir, record)
            execution_id = execution.get("id")
            execution_status = execution.get("status")
            if not isinstance(execution_id, str) or not isinstance(execution_status, str):
                raise campaign_error(
                    f"Campaign execution '{campaign_execution_path}' has invalid execution identity."
                )
            accepted[logical_run_id] = _accepted_observation(
                logical_run_id=logical_run_id,
                run_index=run_index,
                subject_id=logical["subject_id"],
                scenario_id=logical["scenario_id"],
                repetition=logical["repetition"],
                source_execution_id=execution_id,
                source_execution_status=execution_status,
                source_attempt=attempt_number,
                source_run_id=selected_run_id,
                source_session_dir=session_dir,
                source_result_dir=source_result_dir,
                source_campaign_execution_path=campaign_execution_path,
                source_run_record_path=record_path,
            )

    return accepted


def _successful_environment_finalization(result_dir: Path) -> bool:
    path = result_dir / "environment_finalization.yaml"
    if not path.is_file() or path.is_symlink():
        return False
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return False
    return isinstance(value, Mapping) and value.get("ok") is True


def _validate_record_artifacts(result_dir: Path, record: Mapping[str, Any]) -> None:
    for collection, label in (("evidence", "Evidence"), ("diagnostics", "Diagnostic")):
        for entry in record.get(collection, ()):
            if not isinstance(entry, Mapping):
                raise campaign_error(
                    f"Run Record '{result_dir / 'run_record.yaml'}' has invalid {collection}."
                )
            artifact = entry.get("artifact")
            expected = entry.get("sha256")
            if not isinstance(artifact, str) or not isinstance(expected, str):
                raise campaign_error(
                    f"Run Record '{result_dir / 'run_record.yaml'}' has invalid {collection} metadata."
                )
            path = _safe_result_artifact(result_dir, artifact)
            if not path.is_file() or path.is_symlink():
                raise campaign_error(f"{label} artifact does not exist safely: '{path}'.")
            actual = _sha256_file(path)
            if actual != expected:
                raise campaign_error(f"{label} SHA-256 mismatch for '{path}'.")

def _copy_result_tree(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise campaign_error(f"Refusing to merge symbolic-link result directory '{source}'.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        if path.is_symlink():
            raise campaign_error(f"Refusing to merge symbolic-link result artifact '{path}'.")
    shutil.copytree(source, destination, symlinks=False)



def _safe_persisted_result_artifact(session_dir: Path, reference: str) -> Path:
    path = Path(reference)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise campaign_error(f"Unsafe persisted result artifact path: '{reference}'.")
    if path.is_absolute():
        try:
            relative = path.relative_to(session_dir.resolve())
        except ValueError as exc:
            raise campaign_error(
                f"Persisted result artifact escapes its campaign execution: '{reference}'."
            ) from exc
        return _safe_session_artifact(session_dir, relative.as_posix())
    return _safe_session_artifact(session_dir, reference)

def _safe_session_artifact(session_dir: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise campaign_error(f"Unsafe campaign execution artifact path: '{relative}'.")
    candidate = _reject_relative_symlinks(
        session_dir,
        path,
        label="campaign execution artifact",
    )
    try:
        candidate.relative_to(session_dir.resolve())
    except ValueError as exc:
        raise campaign_error(f"Campaign execution artifact escapes its session: '{relative}'.") from exc
    return candidate


def _safe_result_artifact(result_dir: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise campaign_error(f"Unsafe evidence artifact path: '{relative}'.")
    candidate = _reject_relative_symlinks(
        result_dir,
        path,
        label="evidence artifact",
    )
    try:
        candidate.relative_to(result_dir.resolve())
    except ValueError as exc:
        raise campaign_error(f"Evidence artifact escapes its result directory: '{relative}'.") from exc
    return candidate


def _reject_relative_symlinks(root: Path, relative: Path, *, label: str) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise campaign_error(f"Refusing symbolic-link {label} '{current}'.")
    return current.resolve()


def _relative_to_campaign(artifact: campaign_artifact, path: Path) -> str:
    try:
        return path.resolve().relative_to(artifact.path.resolve()).as_posix()
    except ValueError as exc:
        raise campaign_error(f"Campaign source artifact escapes campaign root: '{path}'.") from exc


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise campaign_error(f"Could not load {label} '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise campaign_error(f"{label.capitalize()} '{path}' must contain a mapping.")
    return value


def _load_json_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise campaign_error(f"Could not load {label} '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise campaign_error(f"{label.capitalize()} '{path}' must contain a mapping.")
    return value


def _event_exists(
    events: tuple[dict[str, Any], ...],
    event_type: str,
    *,
    run_index: int,
    attempt: int,
) -> bool:
    return any(
        event.get("event") == event_type
        and event.get("run_index") == run_index
        and event.get("attempt") == attempt
        for event in events
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise campaign_error(f"Could not hash campaign artifact '{path}': {exc}") from exc
    return digest.hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    data = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=False) + "\n").encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def _validate_identifier(label: str, value: str) -> None:
    if not value or value in {".", ".."} or any(char in value for char in "/\\:"):
        raise campaign_error(f"{label.capitalize()} is unsafe for a directory name: '{value}'.")


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise campaign_error("Campaign merge timestamps must be timezone-aware.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "CAMPAIGN_MERGE_SCHEMA_VERSION",
    "campaign_merge_artifact",
    "create_campaign_merge",
    "validate_campaign_merge_manifest",
]
