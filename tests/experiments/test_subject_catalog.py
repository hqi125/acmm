from __future__ import annotations

from acmm.adapters.agent_zero import agent_zero_adapter
from acmm.adapters.hermes import hermes_adapter
from acmm.adapters.open_webui import open_webui_adapter
from acmm.subjects.catalog import (
    lookup_subject_execution_binding,
    registered_subject_ids,
    unsupported_termination_controls,
)


def test_subject_catalog_registers_qualified_subjects() -> None:
    assert registered_subject_ids() == (
        "hermes_nemotron30b",
        "open_webui_nemotron30b",
        "agent_zero_nemotron30b",
    )


def test_subject_catalog_builds_hermes_runtime_adapter() -> None:
    binding = lookup_subject_execution_binding("hermes_nemotron30b")

    assert binding is not None
    adapter = binding.adapter_builder()

    assert isinstance(adapter, hermes_adapter)
    assert ("container", "acmm-hermes") in binding.runtime_details
    assert ("toolset overlay", "acmm") in binding.runtime_details
    assert binding.termination_support.max_steps is True
    assert binding.termination_support.timeout_seconds is True


def test_subject_catalog_builds_open_webui_runtime_adapter() -> None:
    binding = lookup_subject_execution_binding("open_webui_nemotron30b")

    assert binding is not None
    assert isinstance(binding.adapter_builder(), open_webui_adapter)
    assert ("API", "http://127.0.0.1:3000") in binding.runtime_details
    assert binding.termination_support.max_steps is False
    assert binding.termination_support.timeout_seconds is True


def test_subject_catalog_builds_agent_zero_runtime_adapter() -> None:
    binding = lookup_subject_execution_binding("agent_zero_nemotron30b")

    assert binding is not None
    assert isinstance(binding.adapter_builder(), agent_zero_adapter)
    assert ("API", "http://127.0.0.1:50080") in binding.runtime_details
    assert binding.termination_support.max_steps is False
    assert binding.termination_support.timeout_seconds is True


def test_termination_compatibility_accepts_hermes_step_and_timeout_budget() -> None:
    binding = lookup_subject_execution_binding("hermes_nemotron30b")

    assert binding is not None
    assert unsupported_termination_controls(
        binding,
        {"max_steps": 32, "timeout_seconds": 120},
    ) == ()


def test_termination_compatibility_rejects_unenforced_step_budget() -> None:
    for subject_id in (
        "open_webui_nemotron30b",
        "agent_zero_nemotron30b",
    ):
        binding = lookup_subject_execution_binding(subject_id)

        assert binding is not None
        assert unsupported_termination_controls(
            binding,
            {"max_steps": 32, "timeout_seconds": 120},
        ) == ("max_steps",)
        assert unsupported_termination_controls(
            binding,
            {"timeout_seconds": 120},
        ) == ()


def test_subject_catalog_returns_none_for_unknown_subject() -> None:
    assert lookup_subject_execution_binding("unknown_subject") is None
