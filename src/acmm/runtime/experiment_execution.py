from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Callable
import uuid

import yaml

from acmm.runtime.execution import execute_planned_run
from acmm.runtime.planner import build_experiment_plan, planned_run
from acmm.runtime.registry import execution_registry
from acmm.runtime.result_store import persist_executed_run_to


@dataclass(frozen=True)
class run_attempt:
    attempt: int
    run_id: str
    validity: str
    termination: str
    result_path: Path


@dataclass(frozen=True)
class slot_execution:
    planned: planned_run
    attempts: tuple[run_attempt, ...]
    selected_run_id: str | None

    @property
    def is_resolved(self) -> bool:
        return self.selected_run_id is not None


@dataclass(frozen=True)
class experiment_execution_result:
    execution_id: str
    experiment_id: str
    contract_sha256: str
    session_dir: Path
    manifest_path: Path
    contract_run_count: int
    started_at: datetime
    ended_at: datetime
    slots: tuple[slot_execution, ...]

    @property
    def unresolved_run_indices(self) -> tuple[int, ...]:
        return tuple(
            slot.planned.run_index
            for slot in self.slots
            if not slot.is_resolved
        )


def execute_experiment(
    contract: dict,
    contract_path: str | Path,
    registry: execution_registry,
    results_root: str | Path,
    harness_version: str,
    max_attempts_per_run: int = 1,
    run_indices: tuple[int, ...] | None = None,
    progress: Callable[[str], None] | None = None,
    now: Callable[[], datetime] | None = None,
    execution_id_factory: Callable[[], str] | None = None,
    run_id_factory: Callable[[], str] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> experiment_execution_result:
    """
    Execute every planned slot in one experiment execution session.

    Invalid executions are preserved as attempts and retried up to
    ``max_attempts_per_run``. The first scientifically valid execution resolves
    the planned slot. Valid agent outcomes such as timeout are not retried.

    By default the complete Contract plan is executed. ``run_indices`` may
    select a deterministic subset for an explicit engineering/pilot execution;
    the completion manifest records both the full Contract run count and the
    selected run indices so a subset cannot be mistaken for a complete Contract
    execution.

    A completion manifest is published only after the selected plan has been
    traversed. If orchestration or persistence aborts earlier, the session
    directory remains intentionally incomplete and has no completion manifest.
    """

    if (
        not isinstance(max_attempts_per_run, int)
        or isinstance(max_attempts_per_run, bool)
        or max_attempts_per_run < 1
    ):
        raise ValueError("max_attempts_per_run must be an integer >= 1.")

    report = progress if progress is not None else lambda message: None

    clock = now if now is not None else _utc_now
    make_execution_id = (
        execution_id_factory
        if execution_id_factory is not None
        else _new_execution_id
    )
    make_run_id = run_id_factory if run_id_factory is not None else _new_run_id
    wait = sleep if sleep is not None else time.sleep
    inter_run_delay_seconds = _inter_run_delay_seconds(contract)

    source_path = Path(contract_path)
    contract_bytes = source_path.read_bytes()
    contract_sha256 = hashlib.sha256(contract_bytes).hexdigest()

    experiment_id = contract["experiment"]["id"]
    execution_id = make_execution_id()
    plan = build_experiment_plan(contract)
    selected_runs = _select_runs(plan.runs, run_indices)

    _validate_directory_component("experiment ID", experiment_id)
    _validate_directory_component("execution ID", execution_id)

    session_dir = (
        Path(results_root)
        / experiment_id
        / f"execution_{execution_id}"
    )

    if session_dir.exists():
        raise FileExistsError(
            f"Experiment execution directory already exists: '{session_dir}'."
        )

    runs_dir = session_dir / "runs"
    session_dir.mkdir(parents=True)
    runs_dir.mkdir()

    try:
        _write_bytes(
            session_dir / "contract.yaml",
            contract_bytes,
        )
    except Exception:
        _remove_empty_session(session_dir)
        raise

    started_at = clock()

    report(
        f"Execution {execution_id}: selected {len(selected_runs)} of "
        f"{plan.run_count} planned run(s)."
    )

    slots: list[slot_execution] = []

    for selected_position, planned in enumerate(selected_runs):
        attempts: list[run_attempt] = []
        selected_run_id: str | None = None

        for attempt_number in range(1, max_attempts_per_run + 1):
            report(
                f"Run {planned.run_index}: subject={planned.subject_id}, "
                f"scenario={planned.scenario_id}, repetition={planned.repetition}, "
                f"attempt={attempt_number}/{max_attempts_per_run}."
            )

            executed = execute_planned_run(
                contract=contract,
                planned=planned,
                contract_sha256=contract_sha256,
                registry=registry,
                harness_version=harness_version,
                now=clock,
                run_id_factory=make_run_id,
            )

            result_path = persist_executed_run_to(
                executed,
                runs_dir,
            )

            execution = executed.record["execution"]
            run_id = executed.record["run"]["id"]

            report(
                f"Run {planned.run_index} attempt {attempt_number}: "
                f"validity={execution['validity']}, "
                f"termination={execution['termination']}, "
                f"stored={result_path.name}."
            )

            attempts.append(
                run_attempt(
                    attempt=attempt_number,
                    run_id=run_id,
                    validity=execution["validity"],
                    termination=execution["termination"],
                    result_path=result_path,
                )
            )

            if execution["validity"] == "valid":
                selected_run_id = run_id
                break

        slots.append(
            slot_execution(
                planned=planned,
                attempts=tuple(attempts),
                selected_run_id=selected_run_id,
            )
        )

        has_next_selected_run = selected_position + 1 < len(selected_runs)

        if has_next_selected_run and inter_run_delay_seconds > 0:
            report(
                "Pacing: waiting "
                f"{inter_run_delay_seconds:g}s before the next planned run."
            )
            wait(inter_run_delay_seconds)

    ended_at = clock()

    manifest = _build_manifest(
        execution_id=execution_id,
        experiment_id=experiment_id,
        contract_sha256=contract_sha256,
        harness_version=harness_version,
        started_at=started_at,
        ended_at=ended_at,
        max_attempts_per_run=max_attempts_per_run,
        inter_run_delay_seconds=inter_run_delay_seconds,
        contract_run_count=plan.run_count,
        selected_run_indices=tuple(
            planned.run_index
            for planned in selected_runs
        ),
        slots=tuple(slots),
        session_dir=session_dir,
    )

    manifest_path = session_dir / "experiment_execution.yaml"
    _write_yaml_atomically(
        manifest_path,
        manifest,
    )

    report(
        f"Execution {execution_id} complete: "
        f"{len(slots) - len([slot for slot in slots if not slot.is_resolved])}/"
        f"{len(slots)} selected run(s) resolved."
    )

    return experiment_execution_result(
        execution_id=execution_id,
        experiment_id=experiment_id,
        contract_sha256=contract_sha256,
        session_dir=session_dir,
        manifest_path=manifest_path,
        contract_run_count=plan.run_count,
        started_at=started_at,
        ended_at=ended_at,
        slots=tuple(slots),
    )


def _inter_run_delay_seconds(contract: dict) -> float:
    pacing = contract.get("run_protocol", {}).get("pacing", {})
    value = pacing.get("inter_run_delay_seconds", 0)

    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError(
            "run_protocol.pacing.inter_run_delay_seconds must be a "
            "non-negative number."
        )

    return float(value)


def _select_runs(
    runs: tuple[planned_run, ...],
    run_indices: tuple[int, ...] | None,
) -> tuple[planned_run, ...]:
    if run_indices is None:
        return runs

    if not run_indices:
        raise ValueError("run_indices must not be empty when provided.")

    if len(run_indices) != len(set(run_indices)):
        raise ValueError("run_indices must not contain duplicates.")

    by_index = {run.run_index: run for run in runs}
    selected: list[planned_run] = []

    for run_index in run_indices:
        if (
            not isinstance(run_index, int)
            or isinstance(run_index, bool)
            or run_index < 0
        ):
            raise ValueError("run_indices must contain non-negative integers.")

        try:
            selected.append(by_index[run_index])
        except KeyError as exc:
            raise ValueError(
                f"Run index {run_index} is not present in the Contract plan."
            ) from exc

    return tuple(selected)


def _build_manifest(
    execution_id: str,
    experiment_id: str,
    contract_sha256: str,
    harness_version: str,
    started_at: datetime,
    ended_at: datetime,
    max_attempts_per_run: int,
    inter_run_delay_seconds: float,
    contract_run_count: int,
    selected_run_indices: tuple[int, ...],
    slots: tuple[slot_execution, ...],
    session_dir: Path,
) -> dict:
    unresolved = [
        slot.planned.run_index
        for slot in slots
        if not slot.is_resolved
    ]

    slot_records: list[dict] = []

    for slot in slots:
        attempt_records = [
            {
                "attempt": attempt.attempt,
                "run_id": attempt.run_id,
                "validity": attempt.validity,
                "termination": attempt.termination,
                "result_path": attempt.result_path.relative_to(
                    session_dir
                ).as_posix(),
            }
            for attempt in slot.attempts
        ]

        slot_records.append(
            {
                "run_index": slot.planned.run_index,
                "subject_id": slot.planned.subject_id,
                "scenario_id": slot.planned.scenario_id,
                "repetition": slot.planned.repetition,
                "attempts": attempt_records,
                "selected_run_id": slot.selected_run_id,
            }
        )

    return {
        "schema_version": "0.1",
        "execution": {
            "id": execution_id,
            "experiment_id": experiment_id,
            "contract_sha256": contract_sha256,
            "harness_version": harness_version,
            "started_at": _format_timestamp(started_at),
            "ended_at": _format_timestamp(ended_at),
            "max_attempts_per_run": max_attempts_per_run,
            "pacing": {
                "inter_run_delay_seconds": inter_run_delay_seconds,
            },
            "contract_run_count": contract_run_count,
            "selected_run_indices": list(selected_run_indices),
            "planned_run_count": len(slots),
            "resolved_run_count": len(slots) - len(unresolved),
            "unresolved_run_indices": unresolved,
        },
        "slots": slot_records,
    }


def _write_yaml_atomically(
    path: Path,
    data: dict,
) -> None:
    serialized = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=False,
    ).encode("utf-8")

    file_descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(file_descriptor, "wb") as file:
            file.write(serialized)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_path, path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def _write_bytes(
    path: Path,
    content: bytes,
) -> None:
    with path.open("xb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())


def _remove_empty_session(
    session_dir: Path,
) -> None:
    runs_dir = session_dir / "runs"

    if runs_dir.exists():
        runs_dir.rmdir()

    if session_dir.exists():
        session_dir.rmdir()


def _validate_directory_component(
    label: str,
    value: object,
) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"{label.capitalize()} must be a non-empty string."
        )

    if value in {".", ".."} or "/" in value or "\\" in value or ":" in value:
        raise ValueError(
            f"{label.capitalize()} is not safe for a directory name: '{value}'."
        )


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Experiment execution timestamps must be timezone-aware.")

    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_execution_id() -> str:
    return str(uuid.uuid4())


def _new_run_id() -> str:
    return str(uuid.uuid4())
