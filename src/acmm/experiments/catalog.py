from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping, TypeAlias

from acmm.adapters.hermes import hermes_adapter_config
from acmm.harness.capabilities import harness_capabilities
from acmm.pilots.controlled_tool_use import (
    build_controlled_tool_use_hermes_registry,
    controlled_tool_use_harness_capabilities,
)
from acmm.pilots.magic_number import (
    build_magic_number_hermes_registry,
    magic_number_harness_capabilities,
)
from acmm.runtime.registry import execution_registry


experiment_registry_builder: TypeAlias = Callable[
    [hermes_adapter_config | None],
    execution_registry,
]


@dataclass(frozen=True)
class experiment_execution_binding:
    """Execution wiring for one built-in experiment contract."""

    capabilities: harness_capabilities
    registry_builder: experiment_registry_builder


def _build_catalog() -> Mapping[str, experiment_execution_binding]:
    bindings = {
        "magic_number_tool_use_v1": experiment_execution_binding(
            capabilities=magic_number_harness_capabilities(),
            registry_builder=build_magic_number_hermes_registry,
        ),
        "controlled_tool_use_multiscenario_v1": experiment_execution_binding(
            capabilities=controlled_tool_use_harness_capabilities(),
            registry_builder=build_controlled_tool_use_hermes_registry,
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
