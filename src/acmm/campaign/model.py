from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Mapping
import uuid

from jsonschema import Draft202012Validator

from acmm.experiment_package.model import experiment_package
from acmm.generated_paths import default_generated_root
from acmm.experiment_package.provenance import package_provenance
from acmm.experiment_package.staging import (
    open_staged_experiment_package,
    stage_experiment_package,
)
from acmm.runtime.planner import (
    build_experiment_plan,
    build_materialization_slices,
    materialization_key_for_run,
    normalize_materialization_group_by,
)


CAMPAIGN_SCHEMA_VERSION = "0.2"
_CAMPAIGN_SCHEMA_FILES = {
    "0.1": "campaign-v0.1.schema.json",
    "0.2": "campaign-v0.2.schema.json",
}


class campaign_error(ValueError):
    """Raised when a durable campaign cannot be created or loaded safely."""


@dataclass(frozen=True)
class campaign_preview:
    experiment_id: str
    package_sha256: str
    design_digest: str
    run_count: int
    subject_ids: tuple[str, ...]
    scenario_ids: tuple[str, ...]
    repetitions: int
    materialization_group_by: tuple[str, ...]
    materialization_count: int


@dataclass(frozen=True)
class campaign_artifact:
    campaign_id: str
    experiment_id: str
    path: Path
    manifest_path: Path
    manifest: Mapping[str, Any]


def build_campaign_preview(package: experiment_package) -> campaign_preview:
    plan = build_experiment_plan(dict(package.contract))
    group_by = _package_materialization_group_by(package)
    subject_ids = tuple(subject["id"] for subject in package.contract["subjects"])
    materializations = build_materialization_slices(
        plan.runs,
        subject_order=subject_ids,
        group_by=group_by,
    )
    payload = _design_payload(package, plan.runs)
    digest = _sha256_json(payload)
    protocol = package.contract["run_protocol"]
    return campaign_preview(
        experiment_id=package.experiment_id,
        package_sha256=package.package_sha256,
        design_digest=digest,
        run_count=plan.run_count,
        subject_ids=subject_ids,
        scenario_ids=tuple(scenario["id"] for scenario in package.contract["scenarios"]),
        repetitions=protocol["repetitions"],
        materialization_group_by=group_by,
        materialization_count=len(materializations),
    )


def build_campaign_manifest(
    package: experiment_package,
    *,
    campaign_id: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    clock = now if now is not None else _utc_now
    realized_campaign_id = campaign_id if campaign_id is not None else _new_campaign_id()
    _validate_identifier("campaign ID", realized_campaign_id)

    plan = build_experiment_plan(dict(package.contract))
    materialization_group_by = _package_materialization_group_by(package)
    subject_ids = tuple(subject["id"] for subject in package.contract["subjects"])
    materializations = build_materialization_slices(
        plan.runs,
        subject_order=subject_ids,
        group_by=materialization_group_by,
    )
    design_payload = _design_payload(package, plan.runs)
    design_digest = _sha256_json(design_payload)
    provenance = package_provenance(package)
    created_at = clock()
    if created_at.tzinfo is None:
        raise campaign_error("Campaign creation timestamp must be timezone-aware.")

    runs: list[dict[str, Any]] = []
    for planned in plan.runs:
        run_spec = {
            "run_index": planned.run_index,
            "subject_id": planned.subject_id,
            "scenario_id": planned.scenario_id,
            "repetition": planned.repetition,
        }
        runs.append(
            {
                **run_spec,
                "run_id": _logical_run_id(realized_campaign_id, run_spec),
                "materialization_key": materialization_key_for_run(
                    planned,
                    materialization_group_by,
                ),
            }
        )

    protocol = package.contract["run_protocol"]
    manifest = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "campaign": {
            "id": realized_campaign_id,
            "experiment_id": package.experiment_id,
            "created_at": _format_timestamp(created_at),
            "design_digest": design_digest,
            "run_count": plan.run_count,
            "contract_artifact": "contract.yaml",
        },
        "package": {
            "schema_version": provenance.package_schema_version,
            "sha256": provenance.package_sha256,
            "contract_sha256": provenance.contract_sha256,
            "runtime_sha256": provenance.runtime_sha256,
            "snapshot_artifact": "package_store",
            "integrations": [
                {
                    "id": integration.integration_id,
                    "sha256": integration.sha256,
                }
                for integration in sorted(
                    package.integrations,
                    key=lambda item: item.integration_id,
                )
            ],
        },
        "design": {
            "subjects": list(subject_ids),
            "scenarios": [scenario["id"] for scenario in package.contract["scenarios"]],
            "run_protocol": {
                "repetitions": protocol["repetitions"],
                "ordering": protocol.get("ordering", "subject_scenario_repetition"),
                "scenario_ordering": protocol.get("scenario_ordering", "declaration"),
            },
            "materialization": {
                "group_by": list(materialization_group_by),
                "subject_major_within_slice": True,
                "slice_count": len(materializations),
            },
        },
        "runs": runs,
    }
    validate_campaign_manifest(manifest)
    return manifest


def create_campaign(
    package: experiment_package,
    generated_root: str | Path | None = None,
    *,
    campaign_id: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> campaign_artifact:
    root = (
        default_generated_root()
        if generated_root is None
        else Path(generated_root).expanduser().resolve()
    )
    manifest = build_campaign_manifest(
        package,
        campaign_id=campaign_id,
        now=now,
    )
    campaign = manifest["campaign"]
    realized_campaign_id = campaign["id"]
    experiment_id = campaign["experiment_id"]
    _validate_identifier("experiment ID", experiment_id)

    experiment_root = root / experiment_id
    final_dir = experiment_root / f"campaign_{realized_campaign_id}"
    if final_dir.exists():
        raise campaign_error(f"Campaign directory already exists: '{final_dir}'.")

    experiment_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=f".campaign_{realized_campaign_id}.tmp-",
            dir=experiment_root,
        )
    )

    try:
        _write_bytes(temp_dir / "contract.yaml", package.contract_path.read_bytes())
        staged_snapshot = stage_experiment_package(
            package,
            temp_dir / manifest["package"]["snapshot_artifact"],
        )
        if staged_snapshot.package_sha256 != package.package_sha256:
            raise campaign_error(
                "Campaign package snapshot SHA-256 does not match the source package."
            )
        _write_json(temp_dir / "campaign.json", manifest)
        os.rename(temp_dir, final_dir)
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise

    return campaign_artifact(
        campaign_id=realized_campaign_id,
        experiment_id=experiment_id,
        path=final_dir,
        manifest_path=final_dir / "campaign.json",
        manifest=manifest,
    )


def load_campaign(
    campaign_id: str,
    generated_root: str | Path | None = None,
) -> campaign_artifact:
    _validate_identifier("campaign ID", campaign_id)
    root = (
        default_generated_root()
        if generated_root is None
        else Path(generated_root).expanduser().resolve()
    )
    if not root.exists():
        raise campaign_error(f"Generated artifact root does not exist: '{root}'.")
    if root.is_symlink():
        root = root.resolve()
    if not root.is_dir():
        raise campaign_error(f"Generated artifact root is not a directory: '{root}'.")

    matches = tuple(root.glob(f"*/campaign_{campaign_id}"))
    matches = tuple(path for path in matches if path.is_dir() and not path.is_symlink())
    if not matches:
        raise campaign_error(f"Campaign '{campaign_id}' was not found under '{root}'.")
    if len(matches) != 1:
        raise campaign_error(
            f"Campaign ID '{campaign_id}' is ambiguous under generated artifact root '{root}'."
        )

    path = matches[0].resolve()
    manifest_path = path / "campaign.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise campaign_error(
            f"Could not load campaign manifest '{manifest_path}': {exc}"
        ) from exc

    validate_campaign_manifest(manifest)
    campaign = manifest["campaign"]
    if campaign["id"] != campaign_id:
        raise campaign_error(
            f"Campaign directory identity does not match manifest: '{campaign_id}' != "
            f"'{campaign['id']}'."
        )

    contract_path = path / campaign["contract_artifact"]
    try:
        contract_sha256 = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise campaign_error(
            f"Could not read frozen campaign Contract '{contract_path}': {exc}"
        ) from exc
    if contract_sha256 != manifest["package"]["contract_sha256"]:
        raise campaign_error(
            f"Frozen campaign Contract SHA-256 mismatch for '{contract_path}'."
        )

    expected_design_digest = _sha256_json(_manifest_design_payload(manifest))
    if expected_design_digest != campaign["design_digest"]:
        raise campaign_error(
            f"Campaign design digest mismatch for '{manifest_path}'."
        )

    snapshot_root = path / manifest["package"]["snapshot_artifact"]
    try:
        snapshot = open_staged_experiment_package(
            snapshot_root,
            campaign["experiment_id"],
            manifest["package"]["sha256"],
        )
    except (OSError, ValueError) as exc:
        raise campaign_error(
            f"Could not verify frozen campaign package snapshot '{snapshot_root}': {exc}"
        ) from exc
    if snapshot.contract_sha256 != manifest["package"]["contract_sha256"]:
        raise campaign_error(
            "Frozen campaign package Contract SHA-256 does not match the campaign manifest."
        )

    return campaign_artifact(
        campaign_id=campaign_id,
        experiment_id=campaign["experiment_id"],
        path=path,
        manifest_path=manifest_path,
        manifest=manifest,
    )



def load_campaign_package(artifact: campaign_artifact) -> experiment_package:
    snapshot_root = artifact.path / artifact.manifest["package"]["snapshot_artifact"]
    try:
        return open_staged_experiment_package(
            snapshot_root,
            artifact.experiment_id,
            artifact.manifest["package"]["sha256"],
        )
    except (OSError, ValueError) as exc:
        raise campaign_error(
            f"Could not open frozen campaign package snapshot '{snapshot_root}': {exc}"
        ) from exc


def ensure_campaign_package_installed(
    artifact: campaign_artifact,
    runtime_root: str | Path,
) -> experiment_package:
    snapshot = load_campaign_package(artifact)
    try:
        return stage_experiment_package(snapshot, runtime_root)
    except (OSError, ValueError) as exc:
        raise campaign_error(
            "Could not restore the frozen campaign package revision into the runtime "
            f"store: {exc}"
        ) from exc

def validate_campaign_manifest(manifest: Mapping[str, Any]) -> None:
    schema_version = manifest.get("schema_version")
    schema_name = _CAMPAIGN_SCHEMA_FILES.get(schema_version)
    if schema_name is None:
        raise campaign_error(
            f"Unsupported Campaign schema_version '{schema_version}'."
        )
    schema_path = Path(__file__).with_name("schemas") / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not load Campaign schema '{schema_path}': {exc}") from exc

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "$"
        raise campaign_error(
            f"Campaign manifest validation failed at '{location}': {first.message}"
        )

    runs = manifest["runs"]
    indices = [item["run_index"] for item in runs]
    if indices != list(range(len(runs))):
        raise campaign_error(
            "Campaign runs must contain contiguous zero-based run_index values in order."
        )
    run_ids = [item["run_id"] for item in runs]
    if len(run_ids) != len(set(run_ids)):
        raise campaign_error("Campaign runs contain duplicate run_id values.")
    if manifest["campaign"]["run_count"] != len(runs):
        raise campaign_error("Campaign run_count does not match the frozen run table.")
    if schema_version == "0.2":
        group_by = normalize_materialization_group_by(
            tuple(manifest["design"]["materialization"]["group_by"])
        )
        subject_ids = tuple(manifest["design"]["subjects"])
        planned = tuple(
            _planned_run_from_manifest(item)
            for item in runs
        )
        expected = {
            item.run_index: materialization_key_for_run(item, group_by)
            for item in planned
        }
        for item in runs:
            if item["materialization_key"] != expected[item["run_index"]]:
                raise campaign_error(
                    "Campaign run materialization_key does not match frozen grouping."
                )
        slices = build_materialization_slices(
            planned,
            subject_order=subject_ids,
            group_by=group_by,
        )
        if manifest["design"]["materialization"]["slice_count"] != len(slices):
            raise campaign_error(
                "Campaign materialization slice_count does not match the frozen run table."
            )


def _design_payload(package: experiment_package, runs) -> dict[str, Any]:
    provenance = package_provenance(package)
    group_by = _package_materialization_group_by(package)
    return {
        "schema": "acmm.campaign-design/v2",
        "experiment_id": package.experiment_id,
        "package": {
            "schema_version": provenance.package_schema_version,
            "sha256": provenance.package_sha256,
            "contract_sha256": provenance.contract_sha256,
            "runtime_sha256": provenance.runtime_sha256,
            "integrations": [
                {
                    "id": integration.integration_id,
                    "sha256": integration.sha256,
                }
                for integration in sorted(
                    package.integrations,
                    key=lambda item: item.integration_id,
                )
            ],
        },
        "materialization": {
            "group_by": list(group_by),
            "subject_major_within_slice": True,
        },
        "runs": [
            {
                "run_index": item.run_index,
                "subject_id": item.subject_id,
                "scenario_id": item.scenario_id,
                "repetition": item.repetition,
                "materialization_key": materialization_key_for_run(item, group_by),
            }
            for item in runs
        ],
    }


def _manifest_design_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    package = manifest["package"]
    if manifest.get("schema_version") == "0.1":
        return {
            "schema": "acmm.campaign-design/v1",
            "experiment_id": manifest["campaign"]["experiment_id"],
            "package": {
                "schema_version": package["schema_version"],
                "sha256": package["sha256"],
                "contract_sha256": package["contract_sha256"],
                "runtime_sha256": package["runtime_sha256"],
                "integrations": package["integrations"],
            },
            "runs": [
                {
                    "run_index": item["run_index"],
                    "subject_id": item["subject_id"],
                    "scenario_id": item["scenario_id"],
                    "repetition": item["repetition"],
                }
                for item in manifest["runs"]
            ],
        }

    materialization = manifest["design"]["materialization"]
    return {
        "schema": "acmm.campaign-design/v2",
        "experiment_id": manifest["campaign"]["experiment_id"],
        "package": {
            "schema_version": package["schema_version"],
            "sha256": package["sha256"],
            "contract_sha256": package["contract_sha256"],
            "runtime_sha256": package["runtime_sha256"],
            "integrations": package["integrations"],
        },
        "materialization": {
            "group_by": materialization["group_by"],
            "subject_major_within_slice": materialization[
                "subject_major_within_slice"
            ],
        },
        "runs": [
            {
                "run_index": item["run_index"],
                "subject_id": item["subject_id"],
                "scenario_id": item["scenario_id"],
                "repetition": item["repetition"],
                "materialization_key": item["materialization_key"],
            }
            for item in manifest["runs"]
        ],
    }


def _package_materialization_group_by(package: experiment_package) -> tuple[str, ...]:
    configured = package.campaign_materialization
    if configured is None:
        return normalize_materialization_group_by(None)
    return normalize_materialization_group_by(configured.group_by)


def _planned_run_from_manifest(item: Mapping[str, Any]):
    from acmm.runtime.planner import planned_run

    return planned_run(
        run_index=item["run_index"],
        subject_id=item["subject_id"],
        scenario_id=item["scenario_id"],
        repetition=item["repetition"],
    )


def _logical_run_id(campaign_id: str, run_spec: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        _canonical_json_bytes(
            {
                "campaign_id": campaign_id,
                "run": dict(run_spec),
            }
        )
    ).hexdigest()
    return f"run_{run_spec['run_index']:06d}_{digest[:16]}"


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise campaign_error(f"Campaign identity payload is not canonical JSON data: {exc}") from exc
    return text.encode("ascii")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    text = json.dumps(
        value,
        ensure_ascii=True,
        indent=2,
        sort_keys=False,
    ) + "\n"
    _write_bytes(path, text.encode("ascii"))


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def _validate_identifier(label: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise campaign_error(f"{label.capitalize()} must be a non-empty string.")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value) is None:
        raise campaign_error(f"{label.capitalize()} is unsafe for a directory name: '{value}'.")


def _format_timestamp(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_campaign_id() -> str:
    return str(uuid.uuid4())


__all__ = [
    "CAMPAIGN_SCHEMA_VERSION",
    "build_campaign_manifest",
    "build_campaign_preview",
    "campaign_artifact",
    "campaign_error",
    "campaign_preview",
    "create_campaign",
    "default_generated_root",
    "ensure_campaign_package_installed",
    "load_campaign",
    "load_campaign_package",
    "validate_campaign_manifest",
]
