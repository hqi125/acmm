from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, TypeAlias

from acmm.harness.capabilities import harness_capabilities
from acmm.pilots.controlled_tool_use import (
    build_controlled_tool_use_registry,
    controlled_tool_use_harness_capabilities,
)
from acmm.pilots.magic_number import (
    build_magic_number_registry,
    magic_number_harness_capabilities,
)
from acmm.runtime.interfaces import subject_adapter
from acmm.runtime.registry import execution_registry


experiment_registry_builder: TypeAlias = Callable[
    [Mapping[str, subject_adapter]],
    execution_registry,
]


@dataclass(frozen=True)
class experiment_execution_binding:
    """Execution components for one built-in experiment contract."""

    canonical_contract: Path
    capabilities: harness_capabilities
    registry_builder: experiment_registry_builder


def _build_catalog() -> Mapping[str, experiment_execution_binding]:
    bindings = {
        "magic_number_tool_use_v1": experiment_execution_binding(
            canonical_contract=Path(
                "experiments/examples/magic_number/experiment.yaml"
            ),
            capabilities=magic_number_harness_capabilities(),
            registry_builder=build_magic_number_registry,
        ),
        "controlled_tool_use_multiscenario_v1": experiment_execution_binding(
            canonical_contract=Path(
                "experiments/examples/controlled_tool_use_multiscenario/"
                "experiment.yaml"
            ),
            capabilities=controlled_tool_use_harness_capabilities(),
            registry_builder=build_controlled_tool_use_registry,
        ),
    }

    return MappingProxyType(bindings)


_EXPERIMENT_CATALOG = _build_catalog()


def lookup_experiment_execution_binding(
    experiment_id: str,
) -> experiment_execution_binding | None:
    """Return the built-in execution binding for an experiment, if registered."""

    return _EXPERIMENT_CATALOG.get(experiment_id)


def registered_experiment_ids() -> tuple[str, ...]:
    """Return built-in experiment IDs in catalog declaration order."""

    return tuple(_EXPERIMENT_CATALOG)
