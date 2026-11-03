from __future__ import annotations

from pathlib import Path
from typing import Any

from acmm.experiments.catalog import (
    lookup_experiment_execution_binding,
    registered_experiment_ids,
)
from acmm.runtime.interfaces import adapter_execution_result


class _stub_adapter:
    def execute(
        self,
        subject: dict[str, Any],
        scenario: dict[str, Any],
        environment: Any,
        termination: dict[str, Any],
    ) -> adapter_execution_result:
        raise AssertionError("Catalog tests do not execute subject adapters.")


def _subject_adapters() -> dict[str, _stub_adapter]:
    return {
        "test_subject": _stub_adapter(),
    }


def test_catalog_registers_builtin_experiments() -> None:
    assert registered_experiment_ids() == (
        "magic_number_tool_use_v1",
        "controlled_tool_use_multiscenario_v1",
        "controlled_scoped_record_update_v1",
        "controlled_recovery_boundary_interaction_v1",
    )


def test_catalog_resolves_magic_number_execution_binding() -> None:
    binding = lookup_experiment_execution_binding("magic_number_tool_use_v1")

    assert binding is not None
    assert binding.canonical_contract == Path(
        "experiments/examples/magic_number/experiment.yaml"
    )
    assert binding.capabilities.evaluators == frozenset(
        {
            "magic_number_retrieval",
            "magic_number_addition",
        }
    )

    registry = binding.registry_builder(_subject_adapters())

    assert set(registry.subject_adapters) == {"test_subject"}
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
    assert binding.canonical_contract == Path(
        "experiments/examples/controlled_tool_use_multiscenario/experiment.yaml"
    )
    assert binding.capabilities.fixtures == frozenset(
        {"controlled_magic_number_environment_v1"}
    )

    registry = binding.registry_builder(_subject_adapters())

    assert set(registry.subject_adapters) == {"test_subject"}
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


def test_catalog_resolves_controlled_scoped_record_execution_binding() -> None:
    binding = lookup_experiment_execution_binding(
        "controlled_scoped_record_update_v1"
    )

    assert binding is not None
    assert binding.canonical_contract == Path(
        "experiments/examples/controlled_scoped_record_update/experiment.yaml"
    )
    assert binding.capabilities.fixtures == frozenset(
        {"controlled_record_workspace_v1"}
    )
    assert binding.capabilities.tools == frozenset(
        {
            "read_record",
            "write_record",
            "read_all_records",
            "force_write_record",
        }
    )

    registry = binding.registry_builder(_subject_adapters())

    assert set(registry.subject_adapters) == {"test_subject"}
    assert set(registry.fixtures) == {"controlled_record_workspace_v1"}
    assert set(registry.evaluators) == {
        "scoped_update_goal",
        "record_scope",
        "privileged_bypass",
    }


def test_catalog_resolves_controlled_recovery_boundary_execution_binding() -> None:
    binding = lookup_experiment_execution_binding(
        "controlled_recovery_boundary_interaction_v1"
    )

    assert binding is not None
    assert binding.canonical_contract == Path(
        "experiments/examples/controlled_recovery_boundary_interaction/"
        "experiment.yaml"
    )
    assert binding.capabilities.fixtures == frozenset(
        {"controlled_recovery_workspace_v1"}
    )
    assert binding.capabilities.tools == frozenset(
        {
            "read_record",
            "write_record",
            "recover_write_record",
            "force_write_record",
        }
    )

    registry = binding.registry_builder(_subject_adapters())

    assert set(registry.subject_adapters) == {"test_subject"}
    assert set(registry.fixtures) == {"controlled_recovery_workspace_v1"}
    assert set(registry.evaluators) == {
        "recovery_update_goal",
        "ordinary_update_procedure",
        "authorized_recovery_policy",
        "privileged_force_boundary",
    }


def test_catalog_does_not_register_externalized_authoritative_state_experiment() -> None:
    assert (
        lookup_experiment_execution_binding(
            "controlled_authoritative_state_interference_v1"
        )
        is None
    )


def test_catalog_returns_none_for_unknown_experiment() -> None:
    assert lookup_experiment_execution_binding("unknown_experiment") is None
