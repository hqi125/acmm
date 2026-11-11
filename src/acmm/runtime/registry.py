from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from acmm.integration_api import tool_result_error_interpreter
from acmm.runtime.interfaces import (
    environment_fixture,
    opportunity_evaluator,
    runtime_execution_lifecycle,
    runtime_subject_admission_controller,
    subject_adapter,
)


@dataclass(frozen=True)
class execution_registry:
    subject_adapters: dict[str, subject_adapter] = field(default_factory=dict)
    lifecycle: runtime_execution_lifecycle | None = None
    admission_controller: runtime_subject_admission_controller | None = None
    fixtures: dict[str, environment_fixture] = field(default_factory=dict)
    evaluators: dict[str, opportunity_evaluator] = field(default_factory=dict)
    tool_result_error_interpreters: tuple[tool_result_error_interpreter, ...] = ()
    subject_configuration_overlays: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )
