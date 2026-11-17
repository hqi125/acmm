from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Callable, Mapping
import uuid

import yaml

from acmm.runtime.execution import execute_planned_run, finalize_executed_run
from acmm.runtime.execution_manifest import EXPERIMENT_EXECUTION_SCHEMA_VERSION
from acmm.runtime.planner import (
    build_experiment_plan,
    build_materialization_slices,
    materialization_key_for_run,
    planned_run,
)
from acmm.runtime.interfaces import (
    runtime_materialization_context,
    runtime_materialization_run,
    runtime_resource_admission,
    runtime_resource_execution_context,
    runtime_resource_governor_session,
    runtime_resource_subject_slice_context,
    runtime_materialization_finalization,
    runtime_run_context,
    runtime_run_finalization,
    runtime_session_context,
    runtime_session_finalization,
)
from acmm.runtime.provider_pressure import (
    provider_pressure_engine,
    provider_pressure_execution_session,
    provider_pressure_run_admission,
    runtime_provider_pressure_action_result_event,
    runtime_provider_pressure_execution_context,
    runtime_provider_pressure_run_context,
    runtime_provider_pressure_run_outcome,
    runtime_provider_pressure_pacing_event,
    runtime_provider_pressure_recovery_event,
    runtime_provider_pressure_workload,
)
from acmm.runtime.provider_pressure_gate import provider_pressure_transport_gate
from acmm.runtime.provider_usage import execution_provider_usage_monitor
from acmm.runtime.registry import execution_registry
from acmm.runtime.result_store import persist_executed_run_to
from acmm.runtime.resource_usage import (
    EXECUTION_RESOURCE_USAGE_FILENAME,
    EXECUTION_RESOURCE_USAGE_SCHEMA_VERSION,
    discover_execution_resource_usage_history,
    write_execution_resource_usage,
)


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


@dataclass(frozen=True)
class experiment_interruption_result:
    execution_id: str
    experiment_id: str
    contract_sha256: str
    session_dir: Path
    interruption_path: Path
    contract_run_count: int
    selected_run_indices: tuple[int, ...]
    started_at: datetime
    ended_at: datetime
    slots: tuple[slot_execution, ...]
    interrupted_run_index: int | None
    interrupted_attempt: int | None

    @property
    def resolved_run_indices(self) -> tuple[int, ...]:
        return tuple(
            slot.planned.run_index
            for slot in self.slots
            if slot.is_resolved
        )

    @property
    def unresolved_run_indices(self) -> tuple[int, ...]:
        resolved = set(self.resolved_run_indices)
        return tuple(
            run_index
            for run_index in self.selected_run_indices
            if run_index not in resolved
        )

    @property
    def started_run_indices(self) -> tuple[int, ...]:
        return tuple(slot.planned.run_index for slot in self.slots)

    @property
    def not_started_run_indices(self) -> tuple[int, ...]:
        started = set(self.started_run_indices)
        return tuple(
            run_index
            for run_index in self.selected_run_indices
            if run_index not in started
        )


@dataclass(frozen=True)
class experiment_failure_result:
    execution_id: str
    experiment_id: str
    contract_sha256: str
    session_dir: Path
    failure_path: Path
    contract_run_count: int
    selected_run_indices: tuple[int, ...]
    started_at: datetime
    ended_at: datetime
    slots: tuple[slot_execution, ...]
    failed_run_index: int | None
    failed_attempt: int | None
    detail: str

    @property
    def resolved_run_indices(self) -> tuple[int, ...]:
        return tuple(
            slot.planned.run_index
            for slot in self.slots
            if slot.is_resolved
        )

    @property
    def unresolved_run_indices(self) -> tuple[int, ...]:
        resolved = set(self.resolved_run_indices)
        return tuple(
            run_index
            for run_index in self.selected_run_indices
            if run_index not in resolved
        )

    @property
    def started_run_indices(self) -> tuple[int, ...]:
        return tuple(slot.planned.run_index for slot in self.slots)

    @property
    def not_started_run_indices(self) -> tuple[int, ...]:
        started = set(self.started_run_indices)
        return tuple(
            run_index
            for run_index in self.selected_run_indices
            if run_index not in started
        )


class experiment_execution_interrupted(Exception):
    """Raised after a Ctrl+C interruption has been persisted safely."""

    def __init__(self, result: experiment_interruption_result) -> None:
        super().__init__("Experiment execution interrupted by Ctrl+C.")
        self.result = result


class experiment_execution_failed(Exception):
    """Raised after an unrecoverable execution failure has been persisted safely."""

    def __init__(self, result: experiment_failure_result) -> None:
        super().__init__(result.detail)
        self.result = result


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
    before_run: Callable[[planned_run], None] | None = None,
    after_run: Callable[[planned_run], None] | None = None,
    session_ready: Callable[[Path], None] | None = None,
    package_provenance: Mapping[str, str | None] | None = None,
    deployment_reconciliation: Mapping[str, object] | None = None,
    provider_usage_monitor: execution_provider_usage_monitor | None = None,
    session_parent: str | Path | None = None,
    event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
    capture_environment_snapshots: bool = False,
) -> experiment_execution_result:
    """
    Execute every selected planned slot in one experiment execution session.

    Invalid executions are preserved as attempts and retried up to
    ``max_attempts_per_run``. The first scientifically valid execution resolves
    the planned slot. Valid agent outcomes such as timeout are not retried.

    By default the complete Contract plan is executed. ``run_indices`` may
    select a deterministic subset for an explicit engineering/scoped execution;
    the completion manifest records both the full Contract run count and the
    selected run indices so a subset cannot be mistaken for a complete Contract
    execution.

    A completion manifest is published only after the selected plan has been
    traversed. Ctrl+C writes a separate interruption manifest containing only
    already persisted evidence and then raises ``experiment_execution_interrupted``.
    No completion manifest is created for an interrupted session. ``session_ready``
    is called after the result directory and Contract snapshot have been created.
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
    prepare_run = before_run if before_run is not None else lambda planned: None
    finish_run = after_run if after_run is not None else lambda planned: None
    publish_session = session_ready if session_ready is not None else lambda path: None
    emit = event_sink if event_sink is not None else lambda event, payload: None
    inter_run_delay_seconds = _inter_run_delay_seconds(contract)

    source_path = Path(contract_path)
    contract_bytes = source_path.read_bytes()
    contract_sha256 = hashlib.sha256(contract_bytes).hexdigest()

    experiment_id = contract["experiment"]["id"]
    execution_id = make_execution_id()
    plan = build_experiment_plan(contract)
    selected_runs = _select_runs(plan.runs, run_indices)
    materialization_slices = build_materialization_slices(
        selected_runs,
        subject_order=tuple(subject["id"] for subject in contract["subjects"]),
        group_by=registry.materialization_group_by,
    )
    selected_runs = tuple(
        run
        for materialization in materialization_slices
        for run in materialization.runs
    )
    selected_run_indices = tuple(run.run_index for run in selected_runs)
    materialization_runs_by_key = {
        materialization.key: materialization.runs
        for materialization in materialization_slices
    }
    subject_llm_baselines = _selected_subject_llm_baselines(
        selected_runs,
        registry,
    )

    _validate_directory_component("experiment ID", experiment_id)
    _validate_directory_component("execution ID", execution_id)

    session_dir = (
        Path(session_parent) / f"execution_{execution_id}"
        if session_parent is not None
        else Path(results_root) / experiment_id / f"execution_{execution_id}"
    )

    generated_root = _infer_generated_root(
        Path(results_root),
        Path(session_parent) if session_parent is not None else None,
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

    lifecycle_session_context = runtime_session_context(
        experiment_id=experiment_id,
        execution_id=execution_id,
        selected_run_indices=selected_run_indices,
        session_dir=str(session_dir),
        progress=report,
    )

    # Publish the durable session boundary before package lifecycle initialization.
    # Campaign execution uses this callback to establish its journal so abrupt
    # failure during lifecycle setup is still reconstructible.
    publish_session(session_dir)

    lifecycle_session_handle = None
    if registry.lifecycle is not None:
        emit(
            "runtime_session_initialize_started",
            {"execution_id": execution_id},
        )
        lifecycle_session_handle = registry.lifecycle.session_initialize(
            lifecycle_session_context
        )
        emit(
            "runtime_session_initialized",
            {"execution_id": execution_id},
        )

    resource_usage_path = session_dir / EXECUTION_RESOURCE_USAGE_FILENAME
    resource_governor_session: runtime_resource_governor_session | None = None
    resource_usage_document: dict[str, Any] | None = None
    active_resource_admission: runtime_resource_admission | None = None
    if registry.resource_governor is not None:
        history = discover_execution_resource_usage_history(
            session_dir.parent,
            exclude_session=session_dir,
        )
        resource_context = runtime_resource_execution_context(
            experiment_id=experiment_id,
            execution_id=execution_id,
            selected_run_indices=selected_run_indices,
            session_dir=str(session_dir),
            history_artifacts=tuple(str(item.path) for item in history),
            history_records=tuple(item.document for item in history),
            progress=report,
        )
        resource_governor_session = registry.resource_governor.execution_initialize(
            resource_context
        )
        if not isinstance(resource_governor_session, runtime_resource_governor_session):
            raise TypeError(
                "Runtime resource governor execution_initialize() must return "
                "runtime_resource_governor_session."
            )
        resource_usage_document = {
            "schema_version": EXECUTION_RESOURCE_USAGE_SCHEMA_VERSION,
            "execution": {
                "id": execution_id,
                "experiment_id": experiment_id,
                "status": "running",
                "selected_run_indices": list(selected_run_indices),
                "package_sha256": (
                    str(package_provenance.get("sha256"))
                    if isinstance(package_provenance, Mapping)
                    and package_provenance.get("sha256") is not None
                    else None
                ),
                "detail": None,
            },
            "governor": {
                **dict(resource_governor_session.record),
                "subject_slices": [],
            },
            "history_artifacts": [str(item.path) for item in history],
            "materializations": [],
        }
        write_execution_resource_usage(resource_usage_path, resource_usage_document)

    pressure_engine: provider_pressure_engine | None = None
    pressure_session: provider_pressure_execution_session | None = None
    active_pressure_admission: provider_pressure_run_admission | None = None
    pressure_gate: provider_pressure_transport_gate | None = None
    if registry.provider_pressure_model is not None:
        pressure_engine = provider_pressure_engine(
            registry.provider_pressure_model,
            sleep=wait,
            now=clock,
        )
        pressure_context = runtime_provider_pressure_execution_context(
            experiment_id=experiment_id,
            execution_id=execution_id,
            selected_run_indices=selected_run_indices,
            session_dir=str(session_dir),
            generated_root=str(generated_root),
            package_sha256=(
                str(package_provenance.get("sha256"))
                if isinstance(package_provenance, Mapping)
                and package_provenance.get("sha256") is not None
                else None
            ),
            progress=report,
        )
        pressure_session = pressure_engine.execution_initialize(pressure_context)
        emit(
            "provider_pressure_initialized",
            {
                "provider_id": pressure_session.identity.provider_id,
                "model_id": pressure_session.identity.model_id,
                "historical_replays": list(
                    pressure_session.applied_historical_sources
                ),
            },
        )
        gate_configuration = getattr(
            registry.provider_pressure_model,
            "transport_gate_configuration",
            None,
        )
        if callable(gate_configuration):
            configuration = gate_configuration(pressure_context)
            if configuration is not None:
                pressure_gate = provider_pressure_transport_gate(
                    pressure_engine,
                    pressure_session,
                    configuration,
                )
                pressure_gate.start()
                emit(
                    "provider_pressure_gate_started",
                    {
                        "bind_host": configuration.bind_host,
                        "bind_port": configuration.bind_port,
                        "advertised_url": configuration.advertised_url,
                        "control_path": configuration.control_path,
                    },
                )

    materialization_initialize = (
        getattr(registry.lifecycle, "materialization_initialize", None)
        if registry.lifecycle is not None
        else None
    )
    materialization_finalize = (
        getattr(registry.lifecycle, "materialization_finalize", None)
        if registry.lifecycle is not None
        else None
    )
    active_materialization_context: runtime_materialization_context | None = None
    active_materialization_handle = None
    active_resource_subject_slice_key: tuple[str, str] | None = None
    materialization_observable = (
        tuple(registry.materialization_group_by) != ("run_index",)
        or callable(materialization_initialize)
        or callable(materialization_finalize)
    )

    def begin_materialization(key: str) -> None:
        nonlocal active_materialization_context, active_materialization_handle
        nonlocal active_resource_admission, active_resource_subject_slice_key
        runs = materialization_runs_by_key[key]
        active_resource_subject_slice_key = None
        context = runtime_materialization_context(
            experiment_id=experiment_id,
            execution_id=execution_id,
            key=key,
            selected_run_indices=tuple(run.run_index for run in runs),
            subject_ids=tuple(dict.fromkeys(run.subject_id for run in runs)),
            scenario_ids=tuple(dict.fromkeys(run.scenario_id for run in runs)),
            repetitions=tuple(dict.fromkeys(run.repetition for run in runs)),
            session_dir=str(session_dir),
            runs=tuple(
                runtime_materialization_run(
                    run_index=run.run_index,
                    subject_id=run.subject_id,
                    scenario_id=run.scenario_id,
                    repetition=run.repetition,
                )
                for run in runs
            ),
            progress=report,
        )
        if materialization_observable:
            emit(
                "materialization_initialize_started",
                {
                    "key": key,
                    "selected_run_indices": list(context.selected_run_indices),
                },
            )
        active_resource_admission = None
        if registry.resource_governor is not None:
            assert resource_governor_session is not None
            emit(
                "resource_admission_started",
                {
                    "key": key,
                    "selected_run_indices": list(context.selected_run_indices),
                },
            )
            admission = registry.resource_governor.materialization_admit(
                resource_governor_session.handle,
                context,
            )
            if not isinstance(admission, runtime_resource_admission):
                raise TypeError(
                    "Runtime resource governor materialization_admit() must return "
                    "runtime_resource_admission."
                )
            active_resource_admission = admission
            assert resource_usage_document is not None
            resource_usage_document["materializations"].append(
                {
                    "key": key,
                    "selected_run_indices": list(context.selected_run_indices),
                    "admission": dict(admission.record),
                    "usage": None,
                }
            )
            write_execution_resource_usage(
                resource_usage_path,
                resource_usage_document,
            )
            emit(
                "resource_admitted",
                {
                    "key": key,
                    "record": dict(admission.record),
                },
            )
        handle = None
        if callable(materialization_initialize):
            handle = materialization_initialize(
                lifecycle_session_handle,
                context,
            )
        active_materialization_context = context
        active_materialization_handle = handle
        if materialization_observable:
            emit(
                "materialization_initialized",
                {
                    "key": key,
                    "selected_run_indices": list(context.selected_run_indices),
                },
            )
            report(
                f"Materialization {key}: "
                f"{len(context.selected_run_indices)} selected run(s)."
            )

    def finish_materialization(
        *,
        completed: bool,
        interrupted: bool,
        detail: str | None = None,
        suppress_errors: bool = False,
    ) -> None:
        nonlocal active_materialization_context, active_materialization_handle
        nonlocal active_resource_admission, active_resource_subject_slice_key
        context = active_materialization_context
        handle = active_materialization_handle
        if context is None:
            return
        try:
            result = None
            if callable(materialization_finalize):
                result = materialization_finalize(
                    lifecycle_session_handle,
                    context,
                    handle,
                    runtime_materialization_finalization(
                        completed=completed,
                        interrupted=interrupted,
                        detail=detail,
                    ),
                )
            if registry.resource_governor is not None:
                assert resource_governor_session is not None
                assert active_resource_admission is not None
                usage = registry.resource_governor.materialization_finalize(
                    resource_governor_session.handle,
                    context,
                    active_resource_admission.handle,
                    runtime_materialization_finalization(
                        completed=completed,
                        interrupted=interrupted,
                        detail=detail,
                    ),
                )
                if not isinstance(usage, Mapping):
                    raise TypeError(
                        "Runtime resource governor materialization_finalize() must "
                        "return a mapping."
                    )
                assert resource_usage_document is not None
                for item in reversed(resource_usage_document["materializations"]):
                    if item["key"] == context.key and item["usage"] is None:
                        item["usage"] = dict(usage)
                        break
                else:
                    raise RuntimeError(
                        f"Resource usage materialization '{context.key}' is missing."
                    )
                write_execution_resource_usage(
                    resource_usage_path,
                    resource_usage_document,
                )
                emit(
                    "resource_usage_recorded",
                    {
                        "key": context.key,
                        "usage": dict(usage),
                    },
                )
            if materialization_observable:
                emit(
                    "materialization_finalized",
                    {
                        "key": context.key,
                        "completed": completed,
                        "interrupted": interrupted,
                        "result": dict(result) if isinstance(result, Mapping) else {},
                    },
                )
        except Exception as exc:
            if materialization_observable:
                emit(
                    "materialization_finalization_failed",
                    {
                        "key": context.key,
                        "detail": str(exc) or exc.__class__.__name__,
                    },
                )
            if not suppress_errors:
                raise
            report(
                "WARNING: Materialization finalization failed for "
                f"{context.key}: {exc}"
            )
        finally:
            active_materialization_context = None
            active_materialization_handle = None
            active_resource_admission = None
            active_resource_subject_slice_key = None

    def finish_pressure_run(
        *,
        completed: bool,
        resolved: bool,
        attempts: tuple[run_attempt, ...],
        detail: str | None = None,
        suppress_errors: bool = False,
    ) -> None:
        nonlocal active_pressure_admission
        if (
            pressure_engine is None
            or pressure_session is None
            or active_pressure_admission is None
        ):
            return
        try:
            pressure_engine.run_finalize(
                pressure_session,
                active_pressure_admission,
                runtime_provider_pressure_run_outcome(
                    completed=completed,
                    resolved=resolved,
                    attempt_count=len(attempts),
                    terminations=tuple(item.termination for item in attempts),
                    detail=detail,
                ),
            )
            emit(
                "provider_pressure_run_observed",
                {
                    "run_index": active_pressure_admission.context.run_index,
                    "completed": completed,
                    "resolved": resolved,
                },
            )
        except Exception as exc:
            if not suppress_errors:
                raise
            report(
                "WARNING: Provider Pressure run observation failed: "
                f"{exc}"
            )
        finally:
            if pressure_gate is not None:
                pressure_gate.deactivate()
            active_pressure_admission = None

    def finish_pressure_execution(
        status: str,
        *,
        suppress_errors: bool = False,
    ) -> None:
        if pressure_engine is None or pressure_session is None:
            return
        try:
            snapshot_path = pressure_engine.execution_finalize(
                pressure_session,
                status=status,
            )
            emit(
                "provider_pressure_snapshot_exported",
                {
                    "status": status,
                    "path": str(snapshot_path),
                },
            )
        except Exception as exc:
            if not suppress_errors:
                raise
            report(
                "WARNING: Provider Pressure trajectory export failed: "
                f"{exc}"
            )
        finally:
            if pressure_gate is not None:
                pressure_gate.close()

    provider_usage_started = _begin_provider_usage_monitor(
        provider_usage_monitor,
        report,
    )
    started_at = clock()
    emit(
        "execution_started",
        {
            "execution_id": execution_id,
            "selected_run_indices": list(selected_run_indices),
        },
    )

    report(
        f"Execution {execution_id}: selected {len(selected_runs)} of "
        f"{plan.run_count} planned run(s)."
    )

    slots: list[slot_execution] = []
    active_planned: planned_run | None = None
    active_attempts: list[run_attempt] = []
    active_selected_run_id: str | None = None
    active_attempt_number: int | None = None

    try:
        for selected_position, planned in enumerate(selected_runs):
            materialization_key = materialization_key_for_run(
                planned,
                registry.materialization_group_by,
            )
            if (
                active_materialization_context is None
                or active_materialization_context.key != materialization_key
            ):
                if active_materialization_context is not None:
                    finish_materialization(
                        completed=True,
                        interrupted=False,
                    )
                begin_materialization(materialization_key)

            if registry.resource_governor is not None:
                subject_slice_admit = getattr(
                    registry.resource_governor,
                    "subject_slice_admit",
                    None,
                )
                subject_slice_key = (materialization_key, planned.subject_id)
                if (
                    callable(subject_slice_admit)
                    and active_resource_subject_slice_key != subject_slice_key
                ):
                    assert resource_governor_session is not None
                    assert active_materialization_context is not None
                    subject_runs = tuple(
                        run
                        for run in active_materialization_context.runs
                        if run.subject_id == planned.subject_id
                    )
                    subject_context = runtime_resource_subject_slice_context(
                        experiment_id=experiment_id,
                        execution_id=execution_id,
                        materialization_key=materialization_key,
                        subject_id=planned.subject_id,
                        selected_run_indices=tuple(
                            run.run_index for run in subject_runs
                        ),
                        scenario_ids=tuple(
                            dict.fromkeys(run.scenario_id for run in subject_runs)
                        ),
                        repetitions=tuple(
                            dict.fromkeys(run.repetition for run in subject_runs)
                        ),
                        session_dir=str(session_dir),
                        progress=report,
                    )
                    admission = subject_slice_admit(
                        resource_governor_session.handle,
                        subject_context,
                    )
                    if not isinstance(admission, runtime_resource_admission):
                        raise TypeError(
                            "Runtime resource governor subject_slice_admit() must "
                            "return runtime_resource_admission."
                        )
                    assert resource_usage_document is not None
                    governor_record = resource_usage_document.get("governor")
                    if not isinstance(governor_record, dict):
                        raise RuntimeError(
                            "Execution resource governor record is unavailable."
                        )
                    slices = governor_record.setdefault("subject_slices", [])
                    if not isinstance(slices, list):
                        raise RuntimeError(
                            "Execution resource subject-slice record is malformed."
                        )
                    slices.append(
                        {
                            "materialization_key": materialization_key,
                            "subject_id": planned.subject_id,
                            "selected_run_indices": list(
                                subject_context.selected_run_indices
                            ),
                            "admission": dict(admission.record),
                        }
                    )
                    write_execution_resource_usage(
                        resource_usage_path, resource_usage_document
                    )
                    emit(
                        "resource_subject_slice_admitted",
                        {
                            "materialization_key": materialization_key,
                            "subject_id": planned.subject_id,
                            "record": dict(admission.record),
                        },
                    )
                    active_resource_subject_slice_key = subject_slice_key

            active_planned = planned
            active_attempts = []
            active_selected_run_id = None
            active_attempt_number = None

            if pressure_engine is not None and pressure_session is not None:
                pressure_run_context = runtime_provider_pressure_run_context(
                    experiment_id=experiment_id,
                    execution_id=execution_id,
                    run_index=planned.run_index,
                    subject_id=planned.subject_id,
                    scenario_id=planned.scenario_id,
                    repetition=planned.repetition,
                    materialization_key=materialization_key,
                    session_dir=str(session_dir),
                    progress=report,
                )
                active_pressure_admission = pressure_engine.run_admit(
                    pressure_session,
                    pressure_run_context,
                )
                emit(
                    "provider_pressure_run_admitted",
                    dict(active_pressure_admission.record),
                )
                if pressure_gate is not None:
                    pressure_gate.activate(active_pressure_admission.context)
                    emit(
                        "provider_pressure_gate_activated",
                        {
                            "run_index": planned.run_index,
                            "subject_id": planned.subject_id,
                            "scenario_id": planned.scenario_id,
                        },
                    )

            emit(
                "logical_run_started",
                {
                    "run_index": planned.run_index,
                    "subject_id": planned.subject_id,
                    "scenario_id": planned.scenario_id,
                    "repetition": planned.repetition,
                },
            )
            prepare_run(planned)
            emit(
                "logical_run_prepared",
                {"run_index": planned.run_index},
            )

            for attempt_number in range(1, max_attempts_per_run + 1):
                active_attempt_number = attempt_number
                report(
                    f"Run {planned.run_index}: subject={planned.subject_id}, "
                    f"scenario={planned.scenario_id}, repetition={planned.repetition}, "
                    f"attempt={attempt_number}/{max_attempts_per_run}."
                )

                emit(
                    "attempt_started",
                    {
                        "run_index": planned.run_index,
                        "attempt": attempt_number,
                    },
                )

                pressure_action = None
                pressure_pacing = None
                pressure_recovery = None
                pressure_action_result = None
                pressure_checkpoint = None
                if (
                    pressure_engine is not None
                    and pressure_session is not None
                    and active_pressure_admission is not None
                ):
                    pressure_context_for_action = active_pressure_admission.context

                    def pressure_action(
                        action_id: str,
                        features: Mapping[str, float],
                        metadata: Mapping[str, object],
                        *,
                        _pressure_context=pressure_context_for_action,
                    ) -> Mapping[str, object]:
                        admitted = pressure_engine.action_admit(
                            pressure_session,
                            _pressure_context,
                            action_id=action_id,
                            workload=runtime_provider_pressure_workload(
                                features=features,
                                metadata=metadata,
                            ),
                        )
                        emit(
                            "provider_pressure_action_admitted",
                            dict(admitted.record),
                        )
                        return dict(admitted.record)

                    def pressure_pacing(
                        reason: str,
                        duration_seconds: float,
                        action_id: str,
                        features: Mapping[str, float],
                        metadata: Mapping[str, object],
                        *,
                        _pressure_context=pressure_context_for_action,
                    ) -> Mapping[str, object]:
                        event = runtime_provider_pressure_pacing_event(
                            source="runtime",
                            reason=reason,
                            duration_seconds=float(duration_seconds),
                            action_id=action_id,
                            features=features,
                            metadata=metadata,
                        )
                        observation = pressure_engine.pacing_observe(
                            pressure_session,
                            _pressure_context,
                            event,
                        )
                        record = {
                            "reason": event.reason,
                            "duration_seconds": event.duration_seconds,
                            "action_id": event.action_id,
                            "features": dict(event.features),
                            "signal_count": len(observation.signals),
                            "metadata": dict(event.metadata),
                        }
                        emit("provider_pressure_runtime_pacing", record)
                        return record

                    def pressure_recovery(
                        kind: str,
                        duration_seconds: float,
                        recommendation_source: str,
                        action_id: str,
                        decision_id: str | None,
                        features: Mapping[str, float],
                        metadata: Mapping[str, object],
                        *,
                        _pressure_context=pressure_context_for_action,
                    ) -> Mapping[str, object]:
                        event = runtime_provider_pressure_recovery_event(
                            kind=kind,
                            wait_seconds=float(duration_seconds),
                            recommendation_source=recommendation_source,
                            action_id=action_id,
                            decision_id=decision_id,
                            features=features,
                            metadata=metadata,
                        )
                        admitted = pressure_engine.recovery_actuate(
                            pressure_session,
                            _pressure_context,
                            event,
                        )
                        record = dict(admitted.record)
                        emit("provider_pressure_recovery_actuated", record)
                        return record

                    def pressure_action_result(
                        outcome: str,
                        action_id: str,
                        decision_id: str | None,
                        features: Mapping[str, float],
                        metadata: Mapping[str, object],
                        *,
                        _pressure_context=pressure_context_for_action,
                    ) -> Mapping[str, object]:
                        event = runtime_provider_pressure_action_result_event(
                            outcome=outcome,
                            action_id=action_id,
                            decision_id=decision_id,
                            features=features,
                            metadata=metadata,
                        )
                        record = pressure_engine.action_result_observe(
                            pressure_session,
                            _pressure_context,
                            event,
                        )
                        rendered = dict(record)
                        emit("provider_pressure_action_result", rendered)
                        return rendered

                    # Compatibility alias for packages that still call the v0.3
                    # checkpoint API. New packages should use pressure_action.
                    pressure_checkpoint = pressure_action

                lifecycle_run_context = runtime_run_context(
                    execution_id=execution_id,
                    run_index=planned.run_index,
                    subject_id=planned.subject_id,
                    scenario_id=planned.scenario_id,
                    repetition=planned.repetition,
                    attempt=attempt_number,
                    capture_environment_snapshots=capture_environment_snapshots,
                    materialization_key=materialization_key,
                    materialization_handle=active_materialization_handle,
                    progress=report,
                    pressure_action=pressure_action,
                    pressure_pacing=pressure_pacing,
                    pressure_recovery=pressure_recovery,
                    pressure_action_result=pressure_action_result,
                    pressure_checkpoint=pressure_checkpoint,
                )
                executed = execute_planned_run(
                    contract=contract,
                    planned=planned,
                    contract_sha256=contract_sha256,
                    registry=registry,
                    harness_version=harness_version,
                    now=clock,
                    run_id_factory=make_run_id,
                    lifecycle_session_handle=lifecycle_session_handle,
                    lifecycle_context=lifecycle_run_context,
                    event_sink=(
                        lambda event, payload, run_index=planned.run_index,
                        attempt=attempt_number: emit(
                            event,
                            {
                                "run_index": run_index,
                                "attempt": attempt,
                                **dict(payload),
                            },
                        )
                    ),
                )

                execution = executed.record["execution"]
                try:
                    result_path = persist_executed_run_to(
                        executed,
                        runs_dir,
                    )
                except Exception:
                    finalize_executed_run(
                        executed,
                        runtime_run_finalization(
                            persisted=False,
                            validity=execution.get("validity"),
                            termination=execution.get("termination"),
                        ),
                    )
                    raise

                emit(
                    "attempt_persisted",
                    {
                        "run_index": planned.run_index,
                        "attempt": attempt_number,
                        "validity": execution.get("validity"),
                        "termination": execution.get("termination"),
                        "result_path": str(result_path),
                    },
                )

                run_id = executed.record["run"]["id"]
                attempt = run_attempt(
                    attempt=attempt_number,
                    run_id=run_id,
                    validity=execution["validity"],
                    termination=execution["termination"],
                    result_path=result_path,
                )
                # Preserve a durably persisted attempt even if trusted environment
                # finalization subsequently fails.  A failure manifest can then
                # distinguish "scientific evidence exists" from "the execution
                # session could not safely continue."
                active_attempts.append(attempt)

                try:
                    finalization = finalize_executed_run(
                        executed,
                        runtime_run_finalization(
                            persisted=True,
                            validity=execution.get("validity"),
                            termination=execution.get("termination"),
                            result_path=str(result_path),
                        ),
                    )
                except Exception as exc:
                    _write_run_finalization(
                        result_path,
                        {
                            "ok": False,
                            "detail": str(exc) or exc.__class__.__name__,
                        },
                    )
                    raise
                else:
                    if executed.finalizer is not None:
                        _write_run_finalization(
                            result_path,
                            {
                                "ok": True,
                                "result": finalization,
                            },
                        )

                emit(
                    "attempt_finalized",
                    {
                        "run_index": planned.run_index,
                        "attempt": attempt_number,
                    },
                )

                active_attempt_number = None

                if execution["validity"] == "valid":
                    active_selected_run_id = run_id

                report(
                    f"Run {planned.run_index} attempt {attempt_number}: "
                    f"validity={execution['validity']}, "
                    f"termination={execution['termination']}, "
                    f"stored={result_path.name}."
                )

                if active_selected_run_id is not None:
                    break

            slots.append(
                slot_execution(
                    planned=planned,
                    attempts=tuple(active_attempts),
                    selected_run_id=active_selected_run_id,
                )
            )
            active_planned = None
            active_attempts = []
            active_selected_run_id = None
            active_attempt_number = None

            finish_run(planned)
            emit(
                "logical_run_closed",
                {
                    "run_index": planned.run_index,
                    "resolved": bool(slots[-1].is_resolved),
                },
            )
            finish_pressure_run(
                completed=True,
                resolved=bool(slots[-1].is_resolved),
                attempts=tuple(slots[-1].attempts),
            )

            has_next_selected_run = selected_position + 1 < len(selected_runs)

            if has_next_selected_run and inter_run_delay_seconds > 0:
                report(
                    "Pacing: waiting "
                    f"{inter_run_delay_seconds:g}s before the next planned run."
                )
                wait(inter_run_delay_seconds)

        if active_materialization_context is not None:
            finish_materialization(
                completed=True,
                interrupted=False,
            )

        ended_at = clock()
        if resource_usage_document is not None:
            resource_usage_document["execution"]["status"] = "completed"
            resource_usage_document["execution"]["detail"] = None
            write_execution_resource_usage(
                resource_usage_path,
                resource_usage_document,
            )
        finish_pressure_execution("completed")
        provider_usage = _finish_provider_usage_monitor(
            provider_usage_monitor,
            provider_usage_started,
            report,
        )
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
            selected_run_indices=selected_run_indices,
            subject_llm_baselines=subject_llm_baselines,
            package_provenance=package_provenance,
            deployment_reconciliation=deployment_reconciliation,
            provider_usage=provider_usage,
            slots=tuple(slots),
            session_dir=session_dir,
        )

        manifest_path = session_dir / "experiment_execution.yaml"
        _write_yaml_atomically(
            manifest_path,
            manifest,
        )

        if registry.lifecycle is not None:
            registry.lifecycle.session_finalize(
                lifecycle_session_handle,
                lifecycle_session_context,
                runtime_session_finalization(
                    completed=True,
                    interrupted=False,
                ),
            )

        emit(
            "execution_completed",
            {
                "execution_id": execution_id,
                "resolved_run_indices": [
                    slot.planned.run_index for slot in slots if slot.is_resolved
                ],
            },
        )

        report(
            f"Execution {execution_id} complete: "
            f"{len(slots) - len([slot for slot in slots if not slot.is_resolved])}/"
            f"{len(slots)} selected run(s) resolved."
        )
    except KeyboardInterrupt:
        finish_materialization(
            completed=False,
            interrupted=True,
            detail="keyboard_interrupt",
            suppress_errors=True,
        )
        ended_at = clock()
        if resource_usage_document is not None:
            resource_usage_document["execution"]["status"] = "interrupted"
            resource_usage_document["execution"]["detail"] = "keyboard_interrupt"
            write_execution_resource_usage(
                resource_usage_path,
                resource_usage_document,
            )
        finish_pressure_run(
            completed=False,
            resolved=active_selected_run_id is not None,
            attempts=tuple(active_attempts),
            detail="keyboard_interrupt",
            suppress_errors=True,
        )
        finish_pressure_execution("interrupted", suppress_errors=True)
        emit(
            "execution_interrupted",
            {
                "execution_id": execution_id,
                "run_index": (active_planned.run_index if active_planned is not None else None),
                "attempt": active_attempt_number,
                "kind": "keyboard_interrupt",
            },
        )
        provider_usage = _finish_provider_usage_monitor(
            provider_usage_monitor,
            provider_usage_started,
            report,
        )
        interruption_slots = list(slots)

        if active_planned is not None:
            interruption_slots.append(
                slot_execution(
                    planned=active_planned,
                    attempts=tuple(active_attempts),
                    selected_run_id=active_selected_run_id,
                )
            )

        interruption_path = session_dir / "experiment_interruption.yaml"
        interruption = _build_interruption_manifest(
            execution_id=execution_id,
            experiment_id=experiment_id,
            contract_sha256=contract_sha256,
            harness_version=harness_version,
            started_at=started_at,
            ended_at=ended_at,
            max_attempts_per_run=max_attempts_per_run,
            inter_run_delay_seconds=inter_run_delay_seconds,
            contract_run_count=plan.run_count,
            selected_run_indices=selected_run_indices,
            subject_llm_baselines=subject_llm_baselines,
            package_provenance=package_provenance,
            deployment_reconciliation=deployment_reconciliation,
            provider_usage=provider_usage,
            slots=tuple(interruption_slots),
            interrupted_run_index=(
                active_planned.run_index
                if active_planned is not None
                else None
            ),
            interrupted_attempt=active_attempt_number,
            session_dir=session_dir,
        )
        _write_yaml_atomically(interruption_path, interruption)

        if registry.lifecycle is not None:
            try:
                registry.lifecycle.session_finalize(
                    lifecycle_session_handle,
                    lifecycle_session_context,
                    runtime_session_finalization(
                        completed=False,
                        interrupted=True,
                        detail="keyboard_interrupt",
                    ),
                )
            except Exception as exc:
                report(
                    "WARNING: Runtime lifecycle session finalization failed after "
                    f"Ctrl+C: {exc}"
                )

        result = experiment_interruption_result(
            execution_id=execution_id,
            experiment_id=experiment_id,
            contract_sha256=contract_sha256,
            session_dir=session_dir,
            interruption_path=interruption_path,
            contract_run_count=plan.run_count,
            selected_run_indices=selected_run_indices,
            started_at=started_at,
            ended_at=ended_at,
            slots=tuple(interruption_slots),
            interrupted_run_index=(
                active_planned.run_index
                if active_planned is not None
                else None
            ),
            interrupted_attempt=active_attempt_number,
        )

        report(
            f"Execution {execution_id} interrupted: "
            f"{len(result.resolved_run_indices)}/{len(selected_runs)} "
            "selected run(s) resolved before Ctrl+C."
        )
        raise experiment_execution_interrupted(result) from None
    except Exception as exc:
        detail = str(exc) or exc.__class__.__name__
        failed_run_index = (
            active_planned.run_index
            if active_planned is not None
            else None
        )
        failed_attempt = active_attempt_number

        finish_materialization(
            completed=False,
            interrupted=False,
            detail=detail,
            suppress_errors=True,
        )
        if resource_usage_document is not None:
            resource_usage_document["execution"]["status"] = "failed"
            resource_usage_document["execution"]["detail"] = detail
            write_execution_resource_usage(
                resource_usage_path,
                resource_usage_document,
            )
        finish_pressure_run(
            completed=False,
            resolved=active_selected_run_id is not None,
            attempts=tuple(active_attempts),
            detail=detail,
            suppress_errors=True,
        )
        finish_pressure_execution("failed", suppress_errors=True)
        emit(
            "execution_failed",
            {
                "execution_id": execution_id,
                "run_index": failed_run_index,
                "attempt": failed_attempt,
                "detail": detail,
            },
        )

        session_finalize_error = None
        if registry.lifecycle is not None:
            try:
                registry.lifecycle.session_finalize(
                    lifecycle_session_handle,
                    lifecycle_session_context,
                    runtime_session_finalization(
                        completed=False,
                        interrupted=False,
                        detail=detail,
                    ),
                )
            except Exception as finalize_exc:
                session_finalize_error = (
                    str(finalize_exc) or finalize_exc.__class__.__name__
                )
                report(
                    "WARNING: Runtime lifecycle session finalization failed after "
                    f"execution error: {session_finalize_error}"
                )

        ended_at = clock()
        provider_usage = _finish_provider_usage_monitor(
            provider_usage_monitor,
            provider_usage_started,
            report,
        )
        failure_slots = list(slots)
        if active_planned is not None:
            failure_slots.append(
                slot_execution(
                    planned=active_planned,
                    attempts=tuple(active_attempts),
                    selected_run_id=active_selected_run_id,
                )
            )

        failure_detail = detail
        if session_finalize_error is not None:
            failure_detail += (
                "; lifecycle session finalization warning: "
                + session_finalize_error
            )

        failure_path = session_dir / "experiment_failure.yaml"
        failure_manifest = _build_failure_manifest(
            execution_id=execution_id,
            experiment_id=experiment_id,
            contract_sha256=contract_sha256,
            harness_version=harness_version,
            started_at=started_at,
            ended_at=ended_at,
            max_attempts_per_run=max_attempts_per_run,
            inter_run_delay_seconds=inter_run_delay_seconds,
            contract_run_count=plan.run_count,
            selected_run_indices=selected_run_indices,
            subject_llm_baselines=subject_llm_baselines,
            package_provenance=package_provenance,
            deployment_reconciliation=deployment_reconciliation,
            provider_usage=provider_usage,
            slots=tuple(failure_slots),
            failed_run_index=failed_run_index,
            failed_attempt=failed_attempt,
            detail=failure_detail,
            session_dir=session_dir,
        )
        _write_yaml_atomically(failure_path, failure_manifest)

        result = experiment_failure_result(
            execution_id=execution_id,
            experiment_id=experiment_id,
            contract_sha256=contract_sha256,
            session_dir=session_dir,
            failure_path=failure_path,
            contract_run_count=plan.run_count,
            selected_run_indices=selected_run_indices,
            started_at=started_at,
            ended_at=ended_at,
            slots=tuple(failure_slots),
            failed_run_index=failed_run_index,
            failed_attempt=failed_attempt,
            detail=failure_detail,
        )
        report(
            f"Execution {execution_id} failed safely after "
            f"{len(result.resolved_run_indices)}/{len(selected_runs)} selected "
            f"run(s) resolved. Failure record: {failure_path.name}."
        )
        raise experiment_execution_failed(result) from None

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


def _infer_generated_root(
    results_root: Path,
    session_parent: Path | None,
) -> Path:
    if session_parent is not None:
        parent = Path(session_parent).expanduser().resolve()
        if (
            parent.name == "executions"
            and parent.parent.name.startswith("campaign_")
            and len(parent.parents) >= 3
        ):
            return parent.parents[2]
        return parent
    root = Path(results_root).expanduser().resolve()
    if root.name == "results":
        return root.parent
    return root


def _write_run_finalization(
    run_dir: Path,
    payload: Mapping[str, object],
) -> None:
    _write_yaml_atomically(
        Path(run_dir) / "environment_finalization.yaml",
        dict(payload),
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


def _selected_subject_llm_baselines(
    selected_runs: tuple[planned_run, ...],
    registry: execution_registry,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for planned in selected_runs:
        if planned.subject_id in result:
            continue
        overlay = registry.subject_configuration_overlays.get(planned.subject_id, {})
        baseline_id = overlay.get("llm_baseline_id")
        if isinstance(baseline_id, str) and baseline_id:
            result[planned.subject_id] = baseline_id
    return result


def _build_failure_manifest(
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
    subject_llm_baselines: dict[str, str],
    package_provenance: Mapping[str, str | None] | None,
    deployment_reconciliation: Mapping[str, object] | None,
    provider_usage: Mapping[str, object] | None,
    slots: tuple[slot_execution, ...],
    failed_run_index: int | None,
    failed_attempt: int | None,
    detail: str,
    session_dir: Path,
) -> dict:
    resolved = tuple(
        slot.planned.run_index
        for slot in slots
        if slot.is_resolved
    )
    started = tuple(slot.planned.run_index for slot in slots)
    resolved_set = set(resolved)
    started_set = set(started)
    unresolved = tuple(
        run_index
        for run_index in selected_run_indices
        if run_index not in resolved_set
    )
    not_started = tuple(
        run_index
        for run_index in selected_run_indices
        if run_index not in started_set
    )

    execution = {
        "id": execution_id,
        "experiment_id": experiment_id,
        "contract_sha256": contract_sha256,
        "harness_version": harness_version,
        "subject_llm_baselines": dict(subject_llm_baselines),
        "status": "failed",
        "failure_kind": "unrecoverable_execution_error",
        "failure_detail": detail,
        "started_at": _format_timestamp(started_at),
        "failed_at": _format_timestamp(ended_at),
        "max_attempts_per_run": max_attempts_per_run,
        "pacing": {
            "inter_run_delay_seconds": inter_run_delay_seconds,
        },
        "contract_run_count": contract_run_count,
        "selected_run_indices": list(selected_run_indices),
        "selected_run_count": len(selected_run_indices),
        "started_run_indices": list(started),
        "resolved_run_indices": list(resolved),
        "unresolved_run_indices": list(unresolved),
        "not_started_run_indices": list(not_started),
        "failed_run_index": failed_run_index,
        "failed_attempt": failed_attempt,
        "persisted_attempt_count": sum(
            len(slot.attempts)
            for slot in slots
        ),
    }
    _attach_package_provenance(execution, package_provenance)
    _attach_deployment_reconciliation(execution, deployment_reconciliation)
    _attach_provider_usage(execution, provider_usage)

    return {
        "schema_version": EXPERIMENT_EXECUTION_SCHEMA_VERSION,
        "execution": execution,
        "slots": _build_slot_records(slots, session_dir),
    }


def _build_interruption_manifest(
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
    subject_llm_baselines: dict[str, str],
    package_provenance: Mapping[str, str | None] | None,
    deployment_reconciliation: Mapping[str, object] | None,
    provider_usage: Mapping[str, object] | None,
    slots: tuple[slot_execution, ...],
    interrupted_run_index: int | None,
    interrupted_attempt: int | None,
    session_dir: Path,
) -> dict:
    resolved = tuple(
        slot.planned.run_index
        for slot in slots
        if slot.is_resolved
    )
    started = tuple(slot.planned.run_index for slot in slots)
    resolved_set = set(resolved)
    started_set = set(started)
    unresolved = tuple(
        run_index
        for run_index in selected_run_indices
        if run_index not in resolved_set
    )
    not_started = tuple(
        run_index
        for run_index in selected_run_indices
        if run_index not in started_set
    )

    execution = {
        "id": execution_id,
        "experiment_id": experiment_id,
        "contract_sha256": contract_sha256,
        "harness_version": harness_version,
        "subject_llm_baselines": dict(subject_llm_baselines),
        "status": "interrupted",
        "interrupt_kind": "keyboard_interrupt",
        "started_at": _format_timestamp(started_at),
        "interrupted_at": _format_timestamp(ended_at),
        "max_attempts_per_run": max_attempts_per_run,
        "pacing": {
            "inter_run_delay_seconds": inter_run_delay_seconds,
        },
        "contract_run_count": contract_run_count,
        "selected_run_indices": list(selected_run_indices),
        "selected_run_count": len(selected_run_indices),
        "started_run_indices": list(started),
        "resolved_run_indices": list(resolved),
        "unresolved_run_indices": list(unresolved),
        "not_started_run_indices": list(not_started),
        "interrupted_run_index": interrupted_run_index,
        "interrupted_attempt": interrupted_attempt,
        "persisted_attempt_count": sum(
            len(slot.attempts)
            for slot in slots
        ),
    }
    _attach_package_provenance(execution, package_provenance)
    _attach_deployment_reconciliation(execution, deployment_reconciliation)
    _attach_provider_usage(execution, provider_usage)

    return {
        "schema_version": EXPERIMENT_EXECUTION_SCHEMA_VERSION,
        "execution": execution,
        "slots": _build_slot_records(slots, session_dir),
    }


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
    subject_llm_baselines: dict[str, str],
    package_provenance: Mapping[str, str | None] | None,
    deployment_reconciliation: Mapping[str, object] | None,
    provider_usage: Mapping[str, object] | None,
    slots: tuple[slot_execution, ...],
    session_dir: Path,
) -> dict:
    unresolved = [
        slot.planned.run_index
        for slot in slots
        if not slot.is_resolved
    ]

    slot_records = _build_slot_records(slots, session_dir)

    execution = {
        "id": execution_id,
        "experiment_id": experiment_id,
        "contract_sha256": contract_sha256,
        "harness_version": harness_version,
        "subject_llm_baselines": dict(subject_llm_baselines),
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
    }
    _attach_package_provenance(execution, package_provenance)
    _attach_deployment_reconciliation(execution, deployment_reconciliation)
    _attach_provider_usage(execution, provider_usage)

    return {
        "schema_version": EXPERIMENT_EXECUTION_SCHEMA_VERSION,
        "execution": execution,
        "slots": slot_records,
    }


def _begin_provider_usage_monitor(
    monitor: execution_provider_usage_monitor | None,
    report: Callable[[str], None],
) -> bool:
    if monitor is None:
        return False

    try:
        monitor.begin()
    except Exception as exc:
        report(f"Provider usage telemetry unavailable before execution: {exc}")
        return False
    return True


def _finish_provider_usage_monitor(
    monitor: execution_provider_usage_monitor | None,
    started: bool,
    report: Callable[[str], None],
) -> Mapping[str, object] | None:
    if monitor is None or not started:
        return None

    try:
        return dict(monitor.finish())
    except Exception as exc:
        report(f"Provider usage telemetry unavailable after execution: {exc}")
        return None


def _attach_provider_usage(
    execution: dict,
    provider_usage: Mapping[str, object] | None,
) -> None:
    if provider_usage is None:
        return
    execution["provider_usage"] = dict(provider_usage)


def _attach_deployment_reconciliation(
    execution: dict,
    reconciliation: Mapping[str, object] | None,
) -> None:
    if reconciliation is None:
        return
    execution["deployment_reconciliation"] = dict(reconciliation)


def _attach_package_provenance(
    execution: dict,
    provenance: Mapping[str, str | None] | None,
) -> None:
    if provenance is None:
        return
    execution["package"] = {
        "schema_version": provenance["schema_version"],
        "sha256": provenance["sha256"],
        "contract_sha256": provenance["contract_sha256"],
        "runtime_sha256": provenance.get("runtime_sha256"),
    }


def _build_slot_records(
    slots: tuple[slot_execution, ...],
    session_dir: Path,
) -> list[dict]:
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

    return slot_records


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

    generated_root = _infer_generated_root(
        Path(results_root),
        Path(session_parent) if session_parent is not None else None,
    )

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
