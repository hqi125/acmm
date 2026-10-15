from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


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
class prepared_environment:
    handle: Any
    provenance: dict[str, Any]


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


@dataclass(frozen=True)
class executed_run:
    record: dict[str, Any]
    artifacts: tuple[captured_evidence, ...]


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


class opportunity_evaluator(Protocol):
    def evaluate(
        self,
        context: evaluation_context,
    ) -> judgment_result:
        ...
