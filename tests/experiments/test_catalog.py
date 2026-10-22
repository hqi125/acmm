from __future__ import annotations

from acmm.adapters.hermes import hermes_adapter_config
from acmm.experiments.catalog import (
    lookup_experiment_execution_binding,
    registered_experiment_ids,
)


def test_catalog_registers_builtin_experiments() -> None:
    assert registered_experiment_ids() == (
        "magic_number_tool_use_v1",
        "controlled_tool_use_multiscenario_v1",
    )


def test_catalog_resolves_magic_number_execution_binding() -> None:
    binding = lookup_experiment_execution_binding("magic_number_tool_use_v1")

    assert binding is not None
    assert binding.capabilities.evaluators == frozenset(
        {
            "magic_number_retrieval",
            "magic_number_addition",
        }
    )

    registry = binding.registry_builder(hermes_adapter_config())

    assert set(registry.subject_adapters) == {"hermes_nemotron30b"}
    assert set(registry.fixtures) == {"magic_number_environment_v1"}
    assert set(registry.evaluators) == {
        "magic_number_retrieval",
        "magic_number_addition",
    }


def test_catalog_resolves_controlled_tool_use_execution_binding() -> None:
    binding = lookup_experiment_execution_binding(
        "controlled_tool_use_multiscenario_v1"
    )

    assert binding is not None
    assert binding.capabilities.fixtures == frozenset(
        {"controlled_magic_number_environment_v1"}
    )

    registry = binding.registry_builder(hermes_adapter_config())

    assert set(registry.subject_adapters) == {"hermes_nemotron30b"}
    assert set(registry.fixtures) == {"controlled_magic_number_environment_v1"}
    assert set(registry.evaluators) == {
        "authoritative_source_invocation",
        "retrieval_prerequisite",
        "addition_tool_use",
        "successful_result_fidelity",
        "tool_failure_disclosure",
        "unestablished_result_claim",
        "stop_after_tool_failure",
    }


def test_catalog_returns_none_for_unknown_experiment() -> None:
    assert lookup_experiment_execution_binding("unknown_experiment") is None
