from __future__ import annotations

import importlib.util
import json

import pytest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_IDS = (
    "magic_number_tool_use_v1",
    "controlled_tool_use_multiscenario_v1",
)


def _plugin_path(experiment_id: str) -> Path:
    return (
        ROOT
        / "infrastructure"
        / "hermes"
        / "experiments"
        / experiment_id
        / "plugins"
        / "acmm-tools"
        / "__init__.py"
    )


class fake_context:
    def __init__(self) -> None:
        self.tools: dict[str, tuple[str, dict, object]] = {}

    def register_tool(
        self,
        *,
        name: str,
        toolset: str,
        schema: dict,
        handler,
    ) -> None:
        self.tools[name] = (
            toolset,
            schema,
            handler,
        )


def _load_plugin(experiment_id: str):
    spec = importlib.util.spec_from_file_location(
        "acmm_tools_plugin",
        _plugin_path(experiment_id),
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("experiment_id", EXPERIMENT_IDS)
def test_plugin_registers_only_magic_number_pilot_tools(experiment_id: str) -> None:
    module = _load_plugin(experiment_id)
    context = fake_context()

    module.register(context)

    assert set(context.tools) == {
        "get_magic_number",
        "add_numbers",
    }
    assert all(
        registration[0] == "acmm"
        for registration in context.tools.values()
    )


@pytest.mark.parametrize("experiment_id", EXPERIMENT_IDS)
def test_get_magic_number_reads_runtime_fixture_value(
    monkeypatch,
    experiment_id: str,
) -> None:
    module = _load_plugin(experiment_id)
    context = fake_context()
    module.register(context)

    monkeypatch.setenv("ACMM_MAGIC_NUMBER", "87")

    handler = context.tools["get_magic_number"][2]
    result = json.loads(handler({}))

    assert result == {
        "magic_number": 87,
    }


@pytest.mark.parametrize("experiment_id", EXPERIMENT_IDS)
def test_add_numbers_returns_structured_result(experiment_id: str) -> None:
    module = _load_plugin(experiment_id)
    context = fake_context()
    module.register(context)

    handler = context.tools["add_numbers"][2]
    result = json.loads(
        handler(
            {
                "a": 87,
                "b": 13,
            }
        )
    )

    assert result == {
        "result": 100,
    }



@pytest.mark.parametrize("experiment_id", EXPERIMENT_IDS)
def test_get_magic_number_controlled_failure_raises_tool_error(
    monkeypatch,
    experiment_id: str,
) -> None:
    module = _load_plugin(experiment_id)
    context = fake_context()
    module.register(context)

    monkeypatch.setenv("ACMM_MAGIC_NUMBER", "87")
    monkeypatch.setenv("ACMM_RETRIEVAL_MODE", "failure")

    handler = context.tools["get_magic_number"][2]

    with pytest.raises(RuntimeError, match="Controlled ACMM retrieval failure"):
        handler({})


@pytest.mark.parametrize("experiment_id", EXPERIMENT_IDS)
def test_add_numbers_controlled_failure_raises_tool_error(
    monkeypatch,
    experiment_id: str,
) -> None:
    module = _load_plugin(experiment_id)
    context = fake_context()
    module.register(context)

    monkeypatch.setenv("ACMM_ADDITION_MODE", "failure")
    handler = context.tools["add_numbers"][2]

    with pytest.raises(RuntimeError, match="Controlled ACMM addition failure"):
        handler(
            {
                "a": 87,
                "b": 13,
            }
        )
