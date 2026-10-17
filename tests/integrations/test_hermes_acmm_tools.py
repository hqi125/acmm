from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = (
    ROOT
    / "integrations"
    / "hermes"
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


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "acmm_tools_plugin",
        PLUGIN_PATH,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_registers_only_magic_number_pilot_tools() -> None:
    module = _load_plugin()
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


def test_get_magic_number_reads_runtime_fixture_value(
    monkeypatch,
) -> None:
    module = _load_plugin()
    context = fake_context()
    module.register(context)

    monkeypatch.setenv("ACMM_MAGIC_NUMBER", "87")

    handler = context.tools["get_magic_number"][2]
    result = json.loads(handler({}))

    assert result == {
        "magic_number": 87,
    }


def test_add_numbers_returns_structured_result() -> None:
    module = _load_plugin()
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
