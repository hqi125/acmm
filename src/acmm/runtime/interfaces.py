from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from acmm.evidence.interpretation import interpreted_structured_output




@dataclass(frozen=True)
class runtime_diagnostic_check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class runtime_diagnostic_report:
    checks: tuple[runtime_diagnostic_check, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", tuple(self.checks))

    @property
    def is_ready(self) -> bool:
        return all(item.ok for item in self.checks)


@dataclass(frozen=True)
class captured_evidence:
    id: str
    channel: str
    artifact: str
    content: bytes


@dataclass(frozen=True)
class execution_failure:
    kind: str
    message: str


@dataclass(frozen=True)
class runtime_subject_admission_request:
    integration_id: str
    payload: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.integration_id, str) or not self.integration_id.strip():
            raise ValueError("Runtime subject admission integration id must not be empty.")
        object.__setattr__(
            self,
            "payload",
            MappingProxyType(dict(self.payload)),
        )


@dataclass(frozen=True)
class prepared_environment:
    handle: Any
    provenance: dict[str, Any]
    evidence: tuple[captured_evidence, ...] = ()
    task_bindings: Mapping[str, str] = field(default_factory=dict)
    admission_requests: tuple[runtime_subject_admission_request, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "task_bindings", MappingProxyType(dict(self.task_bindings)))
        object.__setattr__(self, "admission_requests", tuple(self.admission_requests))


@dataclass(frozen=True)
class observed_environment:
    evidence: tuple[captured_evidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True)
class adapter_execution_result:
    termination: str
    evidence: tuple[captured_evidence, ...]
    subject_provenance: dict[str, Any] | None
    failure: execution_failure | None = None


@dataclass(frozen=True)
class judgment_result:
    outcome: str
    evidence_ids: tuple[str, ...]
    detail: str | None = None


@dataclass(frozen=True)
class evaluation_context:
    subject: dict[str, Any]
    scenario: dict[str, Any]
    opportunity: dict[str, Any]
    check: dict[str, Any]
    evidence: tuple[captured_evidence, ...]
    interpreted_evidence: Mapping[str, interpreted_structured_output] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class deferred_run_finalizer:
    callback: Callable[[runtime_run_finalization], Mapping[str, Any] | None]


@dataclass(frozen=True)
class executed_run:
    record: dict[str, Any]
    artifacts: tuple[captured_evidence, ...]
    finalizer: deferred_run_finalizer | None = field(
        default=None,
        compare=False,
        repr=False,
    )




@dataclass(frozen=True)
class apparatus_action_progress:
    message: str
    step: int | None = None
    total: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("Apparatus progress message must not be empty.")
        if self.step is not None and (not isinstance(self.step, int) or self.step <= 0):
            raise ValueError("Apparatus progress step must be a positive integer.")
        if self.total is not None and (not isinstance(self.total, int) or self.total <= 0):
            raise ValueError("Apparatus progress total must be a positive integer.")
        if self.step is not None and self.total is not None and self.step > self.total:
            raise ValueError("Apparatus progress step must not exceed total.")


ApparatusProgressReporter = Callable[[apparatus_action_progress], None]


@dataclass(frozen=True)
class apparatus_action_result:
    changed: bool
    detail: str
    observations: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observations",
            MappingProxyType(dict(self.observations)),
        )


class runtime_apparatus_action(Protocol):
    def run(self) -> apparatus_action_result:
        ...


class progressive_runtime_apparatus_action(Protocol):
    def run_with_progress(
        self,
        report: ApparatusProgressReporter,
    ) -> apparatus_action_result:
        ...


class runtime_apparatus_cleanup(Protocol):
    def cleanup(
        self,
        execution_path: str,
        *,
        preview: bool = False,
    ) -> apparatus_action_result:
        ...


class progressive_runtime_apparatus_cleanup(Protocol):
    def cleanup_with_progress(
        self,
        execution_path: str,
        report: ApparatusProgressReporter,
        *,
        preview: bool = False,
    ) -> apparatus_action_result:
        ...


class runtime_diagnostic(Protocol):
    def doctor(self) -> runtime_diagnostic_report:
        ...






@dataclass(frozen=True)
class runtime_subject_admission:
    handle: Any = field(repr=False)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provenance",
            MappingProxyType(dict(self.provenance)),
        )


class runtime_subject_admission_controller(Protocol):
    def admit(
        self,
        subject_id: str,
        requests: tuple[runtime_subject_admission_request, ...],
    ) -> runtime_subject_admission:
        ...

    def revoke(
        self,
        subject_id: str,
        admission: runtime_subject_admission,
    ) -> Mapping[str, Any] | None:
        ...


@dataclass(frozen=True)
class runtime_session_context:
    experiment_id: str
    execution_id: str
    selected_run_indices: tuple[int, ...]
    session_dir: str
    progress: Callable[[str], None] | None = field(
        default=None,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True)
class runtime_materialization_context:
    experiment_id: str
    execution_id: str
    key: str
    selected_run_indices: tuple[int, ...]
    subject_ids: tuple[str, ...]
    scenario_ids: tuple[str, ...]
    repetitions: tuple[int, ...]
    session_dir: str
    progress: Callable[[str], None] | None = field(
        default=None,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True)
class runtime_materialization_finalization:
    completed: bool
    interrupted: bool
    detail: str | None = None


@dataclass(frozen=True)
class runtime_run_context:
    execution_id: str
    run_index: int
    subject_id: str
    scenario_id: str
    repetition: int
    attempt: int
    materialization_key: str | None = None
    materialization_handle: Any = field(
        default=None,
        compare=False,
        repr=False,
    )
    progress: Callable[[str], None] | None = field(
        default=None,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True)
class runtime_run_finalization:
    persisted: bool
    validity: str | None
    termination: str | None
    result_path: str | None = None
    interrupted: bool = False


@dataclass(frozen=True)
class runtime_session_finalization:
    completed: bool
    interrupted: bool
    detail: str | None = None


@dataclass(frozen=True)
class runtime_execution_recovery_context:
    experiment_id: str
    execution_id: str
    selected_run_indices: tuple[int, ...]
    session_dir: str


@dataclass(frozen=True)
class runtime_execution_recovery_result:
    safe_to_continue: bool
    changed: bool
    detail: str
    observations: Mapping[str, Any] = field(default_factory=dict)


class runtime_execution_recovery(Protocol):
    def reconcile(
        self,
        context: runtime_execution_recovery_context,
    ) -> runtime_execution_recovery_result:
        ...


class runtime_materialization_lifecycle(Protocol):
    def materialization_initialize(
        self,
        session_handle: Any,
        context: runtime_materialization_context,
    ) -> Any:
        ...

    def materialization_finalize(
        self,
        session_handle: Any,
        context: runtime_materialization_context,
        handle: Any,
        outcome: runtime_materialization_finalization,
    ) -> Mapping[str, Any] | None:
        ...


class runtime_execution_lifecycle(Protocol):
    def session_initialize(self, context: runtime_session_context) -> Any:
        ...

    def run_prepare(
        self,
        session_handle: Any,
        context: runtime_run_context,
        scenario: dict[str, Any],
        reset: dict[str, Any],
    ) -> prepared_environment:
        ...

    def run_observe(
        self,
        session_handle: Any,
        context: runtime_run_context,
        scenario: dict[str, Any],
        handle: Any,
    ) -> observed_environment:
        ...

    def run_finalize(
        self,
        session_handle: Any,
        context: runtime_run_context,
        scenario: dict[str, Any],
        handle: Any,
        reset: dict[str, Any],
        outcome: runtime_run_finalization,
    ) -> Mapping[str, Any] | None:
        ...

    def session_finalize(
        self,
        session_handle: Any,
        context: runtime_session_context,
        outcome: runtime_session_finalization,
    ) -> Mapping[str, Any] | None:
        ...


class subject_adapter(Protocol):
    def execute(
        self,
        subject: dict[str, Any],
        scenario: dict[str, Any],
        environment: Any,
        termination: dict[str, Any],
    ) -> adapter_execution_result:
        ...


class environment_fixture(Protocol):
    def prepare(
        self,
        scenario: dict[str, Any],
        reset: dict[str, Any],
    ) -> prepared_environment:
        ...


class environment_observer(Protocol):
    def observe(
        self,
        scenario: dict[str, Any],
        handle: Any,
    ) -> observed_environment:
        ...


class environment_finalizer(Protocol):
    def finalize(
        self,
        scenario: dict[str, Any],
        handle: Any,
        reset: dict[str, Any],
    ) -> Mapping[str, Any] | None:
        ...


class opportunity_evaluator(Protocol):
    def evaluate(
        self,
        context: evaluation_context,
    ) -> judgment_result:
        ...
