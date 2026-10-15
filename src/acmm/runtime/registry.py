from __future__ import annotations

from dataclasses import dataclass, field

from acmm.runtime.interfaces import (
    environment_fixture,
    opportunity_evaluator,
    subject_adapter,
)


@dataclass(frozen=True)
class execution_registry:
    subject_adapters: dict[str, subject_adapter] = field(default_factory=dict)
    fixtures: dict[str, environment_fixture] = field(default_factory=dict)
    evaluators: dict[str, opportunity_evaluator] = field(default_factory=dict)
