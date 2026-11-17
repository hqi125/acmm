from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import socket
from pathlib import Path
from typing import Any, Mapping
import uuid

import yaml
from jsonschema import Draft202012Validator

from acmm.campaign.model import campaign_artifact, campaign_error
from acmm.runtime.planner import (
    build_materialization_slices,
    experiment_plan,
    normalize_materialization_group_by,
    planned_run,
    select_planned_runs,
)


CAMPAIGN_EXECUTION_SCHEMA_VERSION = "0.1"
_CAMPAIGN_EXECUTION_SCHEMA_NAME = "campaign-execution-v0.1.schema.json"


@dataclass(frozen=True)
class campaign_run_counts:
    total: int
    pending: int
    active: int
    observed: int
    blocked: int


@dataclass(frozen=True)
class campaign_execution_counts:
    total: int
    completed: int
    interrupted: int
    failed: int
    active: int


@dataclass(frozen=True)
class campaign_recovery_counts:
    required: int
    recovered: int
    failed: int


@dataclass(frozen=True)
class campaign_status_snapshot:
    runs: campaign_run_counts
    executions: campaign_execution_counts
    recovery: campaign_recovery_counts
    pending_run_ids: tuple[str, ...]
    active_run_ids: tuple[str, ...]
    observed_run_ids: tuple[str, ...]
    blocked_run_ids: tuple[str, ...]


@dataclass(frozen=True)
class campaign_event_log:
    events: tuple[dict[str, Any], ...]
    truncated_tail: bool


@dataclass(frozen=True)
class campaign_execution_context:
    artifact: campaign_artifact
    plan: experiment_plan
    selected_runs: tuple[planned_run, ...]
    execution_id: str

    @property
    def session_parent(self) -> Path:
        return self.artifact.path / "executions"

    @property
    def session_dir(self) -> Path:
        return self.session_parent / f"execution_{self.execution_id}"


class campaign_event_journal:
    def __init__(self, path: Path, *, now=None) -> None:
        self.path = Path(path)
        self._sequence = 0
        self._now = now if now is not None else _utc_now

    def emit(self, event_type: str, payload: Mapping[str, Any] | None = None) -> None:
        self._sequence += 1
        event = {
            "sequence": self._sequence,
            "at": _format_timestamp(self._now()),
            "event": event_type,
        }
        if payload:
            event.update(dict(payload))
        _append_json_line(self.path, event)



@contextmanager
def campaign_execution_lock(artifact: campaign_artifact):
    lock_path = artifact.path / ".campaign-execute.lock"
    token = str(uuid.uuid4())
    metadata = {
        "token": token,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "acquired_at": _format_timestamp(_utc_now()),
    }

    for attempt in range(2):
        try:
            descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            existing = _load_lock_metadata(lock_path)
            existing_host = existing.get("host")
            existing_pid = existing.get("pid")
            if (
                existing_host == socket.gethostname()
                and isinstance(existing_pid, int)
                and not isinstance(existing_pid, bool)
                and not _pid_is_alive(existing_pid)
                and attempt == 0
            ):
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if existing_host == socket.gethostname() and isinstance(existing_pid, int):
                raise campaign_error(
                    "Campaign execution is already active in local process "
                    f"{existing_pid}."
                )
            raise campaign_error(
                "Campaign execution lock already exists and cannot be proven stale: "
                f"'{lock_path}'."
            )
        else:
            with os.fdopen(descriptor, "wb") as file:
                payload = (
                    json.dumps(metadata, ensure_ascii=True, indent=2) + "\n"
                ).encode("ascii")
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            break
    else:
        raise campaign_error(f"Could not acquire campaign execution lock '{lock_path}'.")

    try:
        yield
    finally:
        try:
            current = _load_lock_metadata(lock_path)
        except campaign_error:
            current = {}
        if current.get("token") == token:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

def load_frozen_campaign_contract(artifact: campaign_artifact) -> dict[str, Any]:
    contract_path = artifact.path / artifact.manifest["campaign"]["contract_artifact"]
    try:
        loaded = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise campaign_error(
            f"Could not load frozen campaign Contract '{contract_path}': {exc}"
        ) from exc
    if not isinstance(loaded, dict):
        raise campaign_error(
            f"Frozen campaign Contract '{contract_path}' must contain a mapping."
        )
    if loaded.get("experiment", {}).get("id") != artifact.experiment_id:
        raise campaign_error(
            "Frozen campaign Contract experiment identity does not match the campaign."
        )
    return loaded


def frozen_campaign_plan(artifact: campaign_artifact) -> experiment_plan:
    runs = tuple(
        planned_run(
            run_index=item["run_index"],
            subject_id=item["subject_id"],
            scenario_id=item["scenario_id"],
            repetition=item["repetition"],
        )
        for item in artifact.manifest["runs"]
    )
    return experiment_plan(
        experiment_id=artifact.experiment_id,
        runs=runs,
    )



def frozen_campaign_materialization_group_by(
    artifact: campaign_artifact,
) -> tuple[str, ...]:
    if artifact.manifest.get("schema_version") == "0.1":
        return normalize_materialization_group_by(None)
    materialization = artifact.manifest["design"]["materialization"]
    return normalize_materialization_group_by(
        tuple(materialization["group_by"])
    )


def order_campaign_runs_for_execution(
    artifact: campaign_artifact,
    runs: tuple[planned_run, ...],
) -> tuple[planned_run, ...]:
    if not runs:
        return ()
    subject_order = tuple(artifact.manifest["design"]["subjects"])
    slices = build_materialization_slices(
        runs,
        subject_order=subject_order,
        group_by=frozen_campaign_materialization_group_by(artifact),
    )
    return tuple(run for item in slices for run in item.runs)

def select_campaign_runs(
    artifact: campaign_artifact,
    *,
    run_selectors: list[str] | None = None,
    subject_selectors: list[str] | None = None,
    subject_run_selectors: list[str] | None = None,
    scenario_selectors: list[str] | None = None,
    repetition_selectors: list[str] | None = None,
) -> tuple[planned_run, ...]:
    plan = frozen_campaign_plan(artifact)
    contract = load_frozen_campaign_contract(artifact)
    return select_planned_runs(
        plan,
        contract,
        run_selectors=run_selectors,
        subject_selectors=subject_selectors,
        subject_run_selectors=subject_run_selectors,
        scenario_selectors=scenario_selectors,
        repetition_selectors=repetition_selectors,
    )


def create_campaign_execution_context(
    artifact: campaign_artifact,
    selected_runs: tuple[planned_run, ...],
    *,
    execution_id: str | None = None,
) -> campaign_execution_context:
    if not selected_runs:
        raise campaign_error("Campaign execution selection must not be empty.")
    realized_id = execution_id if execution_id is not None else str(uuid.uuid4())
    _validate_identifier("execution ID", realized_id)
    return campaign_execution_context(
        artifact=artifact,
        plan=frozen_campaign_plan(artifact),
        selected_runs=selected_runs,
        execution_id=realized_id,
    )


def initialize_campaign_execution(
    context: campaign_execution_context,
    *,
    started_at: datetime | None = None,
) -> campaign_event_journal:
    if not context.session_dir.is_dir():
        raise campaign_error(
            f"Campaign execution session directory does not exist: '{context.session_dir}'."
        )
    timestamp = started_at if started_at is not None else _utc_now()
    logical_ids = _logical_ids_by_index(context.artifact)
    manifest = {
        "schema_version": CAMPAIGN_EXECUTION_SCHEMA_VERSION,
        "execution": {
            "id": context.execution_id,
            "campaign_id": context.artifact.campaign_id,
            "experiment_id": context.artifact.experiment_id,
            "status": "running",
            "started_at": _format_timestamp(timestamp),
            "selected_run_indices": [run.run_index for run in context.selected_runs],
            "selected_run_ids": [logical_ids[run.run_index] for run in context.selected_runs],
        },
        "outcomes": {
            "observed_run_ids": [],
            "blocked_run_ids": [],
            "pending_selected_run_ids": [
                logical_ids[run.run_index] for run in context.selected_runs
            ],
        },
    }
    validate_campaign_execution_manifest(manifest)
    _write_json_atomically(context.session_dir / "campaign_execution.json", manifest)
    journal = campaign_event_journal(context.session_dir / "campaign_events.jsonl")
    journal.emit(
        "campaign_execution_started",
        {
            "campaign_id": context.artifact.campaign_id,
            "execution_id": context.execution_id,
        },
    )
    return journal


def finalize_campaign_execution(
    context: campaign_execution_context,
    result: Any,
    *,
    status: str,
    ended_at: datetime | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    manifest_path = context.session_dir / "campaign_execution.json"
    manifest = _load_campaign_execution_manifest(manifest_path)
    logical_ids = _logical_ids_by_index(context.artifact)

    observed_indices: set[int] = set()
    blocked_indices: set[int] = set()

    for slot in getattr(result, "slots", ()):
        index = slot.planned.run_index
        if slot.is_resolved:
            observed_indices.add(index)
        elif slot.attempts and _subject_execution_started_for_run(
            context.session_dir,
            index,
        ):
            blocked_indices.add(index)

    interrupted_run_index = getattr(result, "interrupted_run_index", None)
    interrupted_attempt = getattr(result, "interrupted_attempt", None)
    if (
        interrupted_run_index is not None
        and interrupted_attempt is not None
        and interrupted_run_index not in observed_indices
        and _subject_execution_started(
            context.session_dir,
            interrupted_run_index,
            interrupted_attempt,
        )
    ):
        blocked_indices.add(interrupted_run_index)

    selected_indices = tuple(manifest["execution"]["selected_run_indices"])
    observed_ids = [logical_ids[index] for index in selected_indices if index in observed_indices]
    blocked_ids = [
        logical_ids[index]
        for index in selected_indices
        if index in blocked_indices and index not in observed_indices
    ]
    pending_ids = [
        logical_ids[index]
        for index in selected_indices
        if index not in observed_indices and index not in blocked_indices
    ]

    manifest["execution"]["status"] = status
    manifest["execution"]["ended_at"] = _format_timestamp(
        ended_at if ended_at is not None else getattr(result, "ended_at", _utc_now())
    )
    if detail:
        manifest["execution"]["detail"] = detail
    legacy_manifest = getattr(result, "manifest_path", None)
    interruption_manifest = getattr(result, "interruption_path", None)
    failure_manifest = getattr(result, "failure_path", None)
    if legacy_manifest is not None:
        manifest["execution"]["experiment_execution_artifact"] = str(
            Path(legacy_manifest).relative_to(context.session_dir)
        )
    if interruption_manifest is not None:
        manifest["execution"]["experiment_interruption_artifact"] = str(
            Path(interruption_manifest).relative_to(context.session_dir)
        )
    if failure_manifest is not None:
        manifest["execution"]["experiment_failure_artifact"] = str(
            Path(failure_manifest).relative_to(context.session_dir)
        )
    manifest["outcomes"] = {
        "observed_run_ids": observed_ids,
        "blocked_run_ids": blocked_ids,
        "pending_selected_run_ids": pending_ids,
    }
    validate_campaign_execution_manifest(manifest)
    _write_json_atomically(manifest_path, manifest)
    return manifest


def finalize_campaign_execution_failure(
    context: campaign_execution_context,
    *,
    detail: str,
) -> dict[str, Any] | None:
    manifest_path = context.session_dir / "campaign_execution.json"
    if not manifest_path.exists():
        return None
    return _reconcile_one_execution(context.artifact, context.session_dir, detail=detail)


def reconcile_campaign_executions(artifact: campaign_artifact) -> tuple[str, ...]:
    executions_root = artifact.path / "executions"
    if not executions_root.exists():
        return ()
    reconciled: list[str] = []
    for session_dir in sorted(executions_root.glob("execution_*")):
        if not session_dir.is_dir() or session_dir.is_symlink():
            continue
        manifest_path = session_dir / "campaign_execution.json"
        if not manifest_path.exists():
            continue
        manifest = _load_campaign_execution_manifest(manifest_path)
        if manifest["execution"]["campaign_id"] != artifact.campaign_id:
            raise campaign_error(
                f"Campaign execution '{session_dir}' references a different campaign."
            )
        if manifest["execution"]["status"] != "running":
            continue
        _reconcile_one_execution(artifact, session_dir)
        reconciled.append(manifest["execution"]["id"])
    return tuple(reconciled)


def inspect_campaign_status(artifact: campaign_artifact) -> campaign_status_snapshot:
    logical_ids = tuple(item["run_id"] for item in artifact.manifest["runs"])
    observed: set[str] = set()
    blocked: set[str] = set()
    active: set[str] = set()

    execution_total = 0
    execution_completed = 0
    execution_interrupted = 0
    execution_failed = 0
    execution_active = 0
    recovery_required = 0
    recovery_recovered = 0
    recovery_failed = 0

    executions_root = artifact.path / "executions"
    if executions_root.exists():
        for session_dir in sorted(executions_root.glob("execution_*")):
            manifest_path = session_dir / "campaign_execution.json"
            if not manifest_path.is_file():
                continue
            manifest = _load_campaign_execution_manifest(manifest_path)
            execution = manifest["execution"]
            if execution["campaign_id"] != artifact.campaign_id:
                raise campaign_error(
                    f"Campaign execution '{manifest_path}' references a different campaign."
                )
            execution_total += 1
            state = execution["status"]
            if state == "running":
                execution_active += 1
                active.update(execution["selected_run_ids"])
            elif state == "completed":
                execution_completed += 1
            elif state.startswith("interrupted"):
                execution_interrupted += 1
            else:
                execution_failed += 1
            recovery = manifest.get("recovery")
            if isinstance(recovery, Mapping):
                recovery_state = recovery.get("status")
                if recovery_state == "required":
                    recovery_required += 1
                elif recovery_state == "recovered":
                    recovery_recovered += 1
                elif recovery_state == "failed":
                    recovery_failed += 1
            outcomes = manifest["outcomes"]
            observed.update(outcomes["observed_run_ids"])
            blocked.update(outcomes["blocked_run_ids"])

    blocked.difference_update(observed)
    active.difference_update(observed)
    active.difference_update(blocked)
    pending = set(logical_ids) - observed - blocked - active

    ordered = lambda values: tuple(run_id for run_id in logical_ids if run_id in values)
    return campaign_status_snapshot(
        runs=campaign_run_counts(
            total=len(logical_ids),
            pending=len(pending),
            active=len(active),
            observed=len(observed),
            blocked=len(blocked),
        ),
        executions=campaign_execution_counts(
            total=execution_total,
            completed=execution_completed,
            interrupted=execution_interrupted,
            failed=execution_failed,
            active=execution_active,
        ),
        recovery=campaign_recovery_counts(
            required=recovery_required,
            recovered=recovery_recovered,
            failed=recovery_failed,
        ),
        pending_run_ids=ordered(pending),
        active_run_ids=ordered(active),
        observed_run_ids=ordered(observed),
        blocked_run_ids=ordered(blocked),
    )


def eligible_campaign_runs(
    artifact: campaign_artifact,
    selected_runs: tuple[planned_run, ...],
    status: campaign_status_snapshot,
) -> tuple[planned_run, ...]:
    logical_ids = _logical_ids_by_index(artifact)
    pending = set(status.pending_run_ids)
    return tuple(run for run in selected_runs if logical_ids[run.run_index] in pending)


def campaign_run_id(artifact: campaign_artifact, run_index: int) -> str:
    try:
        return _logical_ids_by_index(artifact)[run_index]
    except KeyError as exc:
        raise campaign_error(f"Campaign does not contain run index {run_index}.") from exc


def validate_campaign_execution_manifest(manifest: Mapping[str, Any]) -> None:
    schema_path = Path(__file__).with_name("schemas") / _CAMPAIGN_EXECUTION_SCHEMA_NAME
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not load Campaign Execution schema '{schema_path}': {exc}"
        ) from exc
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "$"
        raise campaign_error(
            f"Campaign execution validation failed at '{location}': {first.message}"
        )


def _reconcile_one_execution(
    artifact: campaign_artifact,
    session_dir: Path,
    *,
    detail: str | None = None,
) -> dict[str, Any]:
    manifest_path = session_dir / "campaign_execution.json"
    manifest = _load_campaign_execution_manifest(manifest_path)
    logical_ids = _logical_ids_by_index(artifact)
    selected_indices = tuple(manifest["execution"]["selected_run_indices"])

    persisted_valid_indices: set[int] = set()
    exposed_indices: set[int] = set()
    finalized_indices: set[int] = set()
    runs_dir = session_dir / "runs"
    if runs_dir.exists():
        for record_path in runs_dir.glob("run_*/run_record.yaml"):
            try:
                record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                continue
            if not isinstance(record, dict):
                continue
            run = record.get("run", {})
            execution = record.get("execution", {})
            index = run.get("run_index")
            if not isinstance(index, int) or isinstance(index, bool):
                continue
            if execution.get("validity") == "valid":
                persisted_valid_indices.add(index)

    event_log = read_campaign_events(session_dir / "campaign_events.jsonl")
    for event in event_log.events:
        index = event.get("run_index")
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        if event.get("event") == "subject_execution_started":
            exposed_indices.add(index)
        elif event.get("event") == "attempt_finalized":
            finalized_indices.add(index)

    observed_indices = persisted_valid_indices & finalized_indices
    blocked_indices = exposed_indices - observed_indices
    observed_ids = [logical_ids[index] for index in selected_indices if index in observed_indices]
    blocked_ids = [logical_ids[index] for index in selected_indices if index in blocked_indices]
    pending_ids = [
        logical_ids[index]
        for index in selected_indices
        if index not in observed_indices and index not in blocked_indices
    ]

    if (session_dir / "experiment_execution.yaml").is_file():
        state = "completed"
        manifest["execution"]["experiment_execution_artifact"] = "experiment_execution.yaml"
    elif (session_dir / "experiment_interruption.yaml").is_file():
        state = "interrupted"
        manifest["execution"]["experiment_interruption_artifact"] = "experiment_interruption.yaml"
    elif (session_dir / "experiment_failure.yaml").is_file():
        state = "failed"
        manifest["execution"]["experiment_failure_artifact"] = "experiment_failure.yaml"
    else:
        state = "interrupted_unclean"

    manifest["execution"]["status"] = state
    manifest["execution"]["ended_at"] = _format_timestamp(_utc_now())
    if detail:
        manifest["execution"]["detail"] = detail
    elif state == "interrupted_unclean":
        manifest["execution"]["detail"] = (
            "reconciled unfinished execution at the last durable journal checkpoint"
            + ("; incomplete trailing journal event ignored" if event_log.truncated_tail else "")
        )
    manifest["outcomes"] = {
        "observed_run_ids": observed_ids,
        "blocked_run_ids": blocked_ids,
        "pending_selected_run_ids": pending_ids,
    }
    validate_campaign_execution_manifest(manifest)
    _write_json_atomically(manifest_path, manifest)
    return manifest




def _subject_execution_started_for_run(
    session_dir: Path,
    run_index: int,
) -> bool:
    for event in read_campaign_events(session_dir / "campaign_events.jsonl").events:
        if event.get("event") != "subject_execution_started":
            continue
        if event.get("run_index") == run_index:
            return True
    return False


def _subject_execution_started(
    session_dir: Path,
    run_index: int,
    attempt: int,
) -> bool:
    for event in read_campaign_events(session_dir / "campaign_events.jsonl").events:
        if event.get("event") != "subject_execution_started":
            continue
        if event.get("run_index") == run_index and event.get("attempt") == attempt:
            return True
    return False

def _load_lock_metadata(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise campaign_error(f"Could not read campaign execution lock '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise campaign_error(f"Campaign execution lock '{path}' is malformed.")
    return value


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True

def _logical_ids_by_index(artifact: campaign_artifact) -> dict[int, str]:
    return {item["run_index"]: item["run_id"] for item in artifact.manifest["runs"]}


def _load_campaign_execution_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise campaign_error(f"Could not load Campaign Execution '{path}': {exc}") from exc
    validate_campaign_execution_manifest(manifest)
    return manifest


def read_campaign_events(path: Path) -> campaign_event_log:
    if not path.is_file():
        return campaign_event_log(events=(), truncated_tail=False)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise campaign_error(f"Could not read campaign event journal '{path}': {exc}") from exc

    truncated_tail = bool(data) and not data.endswith(b"\n")
    lines = data.splitlines()
    events: list[dict[str, Any]] = []
    expected_sequence = 1
    for position, raw_line in enumerate(lines):
        is_last = position == len(lines) - 1
        if is_last and truncated_tail:
            break
        try:
            line = raw_line.decode("ascii")
            item = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise campaign_error(
                f"Campaign event journal '{path}' is corrupted before its durable tail."
            ) from exc
        if not isinstance(item, dict):
            raise campaign_error(
                f"Campaign event journal '{path}' contains a non-object event."
            )
        if item.get("sequence") != expected_sequence:
            raise campaign_error(
                f"Campaign event journal '{path}' sequence is discontinuous at "
                f"{expected_sequence}."
            )
        expected_sequence += 1
        events.append(item)
    return campaign_event_log(events=tuple(events), truncated_tail=truncated_tail)


def _append_json_line(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=True, sort_keys=False) + "\n").encode("ascii")
    with path.open("ab") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def _write_json_atomically(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    data = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=False) + "\n"
    ).encode("ascii")
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
        raise campaign_error("Campaign execution timestamps must be timezone-aware.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "CAMPAIGN_EXECUTION_SCHEMA_VERSION",
    "campaign_event_journal",
    "campaign_event_log",
    "read_campaign_events",
    "campaign_execution_lock",
    "campaign_execution_context",
    "campaign_status_snapshot",
    "campaign_run_counts",
    "campaign_execution_counts",
    "campaign_recovery_counts",
    "campaign_run_id",
    "create_campaign_execution_context",
    "eligible_campaign_runs",
    "finalize_campaign_execution",
    "finalize_campaign_execution_failure",
    "frozen_campaign_plan",
    "initialize_campaign_execution",
    "inspect_campaign_status",
    "load_frozen_campaign_contract",
    "reconcile_campaign_executions",
    "select_campaign_runs",
    "validate_campaign_execution_manifest",
]
