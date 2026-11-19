from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

from acmm.campaign.execution import (
    reconcile_campaign_executions,
    validate_campaign_execution_manifest,
)
from acmm.campaign.model import (
    campaign_artifact,
    campaign_error,
    load_campaign_package,
)
from acmm.experiment_package.runtime import (
    experiment_runtime_error,
    load_staged_experiment_runtime,
)
from acmm.runtime.interfaces import (
    runtime_execution_recovery_context,
    runtime_execution_recovery_result,
)


@dataclass(frozen=True)
class campaign_reconciliation_result:
    locally_reconciled_execution_ids: tuple[str, ...]
    externally_recovered_execution_ids: tuple[str, ...]
    recovery_not_required_execution_ids: tuple[str, ...]


def reconcile_campaign(artifact: campaign_artifact) -> campaign_reconciliation_result:
    locally_reconciled = reconcile_campaign_executions(artifact)
    candidates = _recovery_candidates(artifact)
    if not candidates:
        return campaign_reconciliation_result(
            locally_reconciled_execution_ids=locally_reconciled,
            externally_recovered_execution_ids=(),
            recovery_not_required_execution_ids=(),
        )

    try:
        package = load_campaign_package(artifact)
        loaded = load_staged_experiment_runtime(package) if package.runtime is not None else None
    except (experiment_runtime_error, OSError, ValueError) as exc:
        raise campaign_error(
            "Could not load the frozen Experiment Package runtime required for "
            f"campaign recovery: {exc}"
        ) from exc

    if loaded is None or loaded.runtime.lifecycle is None:
        for session_dir, manifest in candidates:
            _write_recovery(
                session_dir,
                manifest,
                status="not_required",
                detail="frozen Experiment Package does not use runtime execution lifecycle",
                changed=False,
            )
        return campaign_reconciliation_result(
            locally_reconciled_execution_ids=locally_reconciled,
            externally_recovered_execution_ids=(),
            recovery_not_required_execution_ids=tuple(
                manifest["execution"]["id"] for _, manifest in candidates
            ),
        )

    recovery = loaded.runtime.recovery
    if recovery is None:
        execution_ids = tuple(manifest["execution"]["id"] for _, manifest in candidates)
        detail = (
            "unclean execution used runtime lifecycle but the frozen Experiment Package "
            "does not provide runtime recovery support"
        )
        for session_dir, manifest in candidates:
            _write_recovery(
                session_dir,
                manifest,
                status="required",
                detail=detail,
            )
        raise campaign_error(
            "Campaign recovery is required before new logical runs can execute, but "
            "the frozen Experiment Package has no recovery hook. Unclean execution(s): "
            + ", ".join(execution_ids)
            + "."
        )

    recovered_ids: list[str] = []
    for session_dir, manifest in candidates:
        execution = manifest["execution"]
        context = runtime_execution_recovery_context(
            experiment_id=artifact.experiment_id,
            execution_id=execution["id"],
            selected_run_indices=tuple(execution["selected_run_indices"]),
            session_dir=str(session_dir),
        )
        try:
            result = recovery.reconcile(context)
        except KeyboardInterrupt:
            _write_recovery(
                session_dir,
                manifest,
                status="required",
                detail="keyboard_interrupt_during_recovery",
            )
            raise
        except Exception as exc:
            detail = str(exc) or exc.__class__.__name__
            _write_recovery(
                session_dir,
                manifest,
                status="failed",
                detail=detail,
            )
            raise campaign_error(
                f"Campaign external-state recovery failed for execution "
                f"'{execution['id']}': {detail}"
            ) from exc
        validation_error = _recovery_result_error(result)
        if validation_error is not None:
            _write_recovery(
                session_dir,
                manifest,
                status="failed",
                detail=validation_error,
            )
            raise campaign_error(
                f"Campaign external-state recovery failed for execution "
                f"'{execution['id']}': {validation_error}."
            )
        assert isinstance(result, runtime_execution_recovery_result)
        if not result.safe_to_continue:
            _write_recovery(
                session_dir,
                manifest,
                status="failed",
                detail=result.detail,
                changed=result.changed,
                observations=result.observations,
            )
            raise campaign_error(
                f"Campaign recovery did not establish a safe continuation boundary for "
                f"execution '{execution['id']}': {result.detail}"
            )
        _write_recovery(
            session_dir,
            manifest,
            status="recovered",
            detail=result.detail,
            changed=result.changed,
            observations=result.observations,
        )
        recovered_ids.append(execution["id"])

    return campaign_reconciliation_result(
        locally_reconciled_execution_ids=locally_reconciled,
        externally_recovered_execution_ids=tuple(recovered_ids),
        recovery_not_required_execution_ids=(),
    )



def _recovery_result_error(result: Any) -> str | None:
    if not isinstance(result, runtime_execution_recovery_result):
        return "runtime recovery hook returned an invalid result type"
    if type(result.safe_to_continue) is not bool:
        return "runtime recovery result safe_to_continue must be a boolean"
    if type(result.changed) is not bool:
        return "runtime recovery result changed must be a boolean"
    if not isinstance(result.detail, str) or not result.detail.strip():
        return "runtime recovery result detail must be a non-empty string"
    if not isinstance(result.observations, Mapping):
        return "runtime recovery result observations must be a mapping"
    if any(not isinstance(key, str) for key in result.observations):
        return "runtime recovery result observation keys must be strings"
    try:
        json.dumps(dict(result.observations), ensure_ascii=True)
    except (TypeError, ValueError):
        return "runtime recovery result observations must be JSON-serializable"
    return None

def _recovery_candidates(
    artifact: campaign_artifact,
) -> tuple[tuple[Path, dict[str, Any]], ...]:
    executions_root = artifact.path / "executions"
    if not executions_root.is_dir():
        return ()
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for session_dir in sorted(executions_root.glob("execution_*")):
        if not session_dir.is_dir() or session_dir.is_symlink():
            continue
        manifest_path = session_dir / "campaign_execution.json"
        if not manifest_path.is_file():
            continue
        manifest = _load_manifest(manifest_path)
        execution = manifest["execution"]
        if execution["campaign_id"] != artifact.campaign_id:
            raise campaign_error(
                f"Campaign execution '{manifest_path}' references a different campaign."
            )
        if execution["status"] != "interrupted_unclean":
            continue
        recovery = manifest.get("recovery")
        if isinstance(recovery, Mapping) and recovery.get("status") in {
            "recovered",
            "not_required",
        }:
            continue
        candidates.append((session_dir, manifest))
    return tuple(candidates)


def _write_recovery(
    session_dir: Path,
    manifest: dict[str, Any],
    *,
    status: str,
    detail: str,
    changed: bool | None = None,
    observations: Mapping[str, Any] | None = None,
) -> None:
    recovery: dict[str, Any] = {
        "status": status,
        "attempted_at": _format_timestamp(datetime.now(timezone.utc)),
        "detail": detail,
    }
    if changed is not None:
        recovery["changed"] = bool(changed)
    if observations is not None:
        recovery["observations"] = dict(observations)
    manifest["recovery"] = recovery
    validate_campaign_execution_manifest(manifest)
    _write_json_atomically(session_dir / "campaign_execution.json", manifest)


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise campaign_error(f"Could not load Campaign Execution '{path}': {exc}") from exc
    if not isinstance(manifest, dict):
        raise campaign_error(f"Campaign Execution '{path}' must contain a mapping.")
    validate_campaign_execution_manifest(manifest)
    return manifest


def _write_json_atomically(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        data = (
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=False) + "\n"
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise campaign_error(
            f"Campaign recovery metadata is not JSON-serializable: {exc}"
        ) from exc
    try:
        with temporary.open("wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise campaign_error(
            f"Could not persist campaign recovery metadata '{path}': {exc}"
        ) from exc


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "campaign_reconciliation_result",
    "reconcile_campaign",
]
