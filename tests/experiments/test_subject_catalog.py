from __future__ import annotations

from acmm.adapters.hermes import hermes_adapter
from acmm.subjects.catalog import (
    lookup_subject_execution_binding,
    registered_subject_ids,
)


def test_subject_catalog_registers_qualified_subjects() -> None:
    assert registered_subject_ids() == ("hermes_nemotron30b",)


def test_subject_catalog_builds_hermes_runtime_adapter() -> None:
    binding = lookup_subject_execution_binding("hermes_nemotron30b")

    assert binding is not None
    adapter = binding.adapter_builder()

    assert isinstance(adapter, hermes_adapter)
    assert "Hermes container: acmm-hermes" in binding.runtime_description
    assert "toolset overlay: acmm" in binding.runtime_description


def test_subject_catalog_returns_none_for_unknown_subject() -> None:
    assert lookup_subject_execution_binding("unknown_subject") is None
