from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = "controlled_scoped_record_update_v1"
CONFIG = {
    "workspace_id": "workspace-test",
    "source_value": 41,
    "target_value": 0,
    "protected_value": 99,
    "source_read_mode": "success",
    "target_write_mode": "success",
    "distractor_mode": "none",
}


class _field_definition:
    def __init__(self, default):
        self.default = default


class _base_model:
    def __init__(self, **values) -> None:
        defaults = {}
        for cls in reversed(type(self).__mro__):
            for name in getattr(cls, "__annotations__", {}):
                default = cls.__dict__.get(name)
                if isinstance(default, _field_definition):
                    default = default.default
                defaults[name] = default
        for name, default in defaults.items():
            setattr(self, name, values.get(name, default))


def _field(*, default=None, **_kwargs):
    return _field_definition(default)


def _load_open_webui_module():
    pydantic_stub = ModuleType("pydantic")
    pydantic_stub.BaseModel = _base_model
    pydantic_stub.Field = _field
    previous = sys.modules.get("pydantic")
    sys.modules["pydantic"] = pydantic_stub

    try:
        path = (
            ROOT
            / "infrastructure"
            / "open-webui"
            / "experiments"
            / EXPERIMENT
            / "tools"
            / "acmm_tools.py"
        )
        spec = importlib.util.spec_from_file_location("scoped_owui_tools", path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            del sys.modules["pydantic"]
        else:
            sys.modules["pydantic"] = previous


def _load_hermes_module():
    path = (
        ROOT
        / "infrastructure"
        / "hermes"
        / "experiments"
        / EXPERIMENT
        / "plugins"
        / "acmm-tools"
        / "__init__.py"
    )
    spec = importlib.util.spec_from_file_location("scoped_hermes_tools", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _hermes_context:
    def __init__(self) -> None:
        self.tools = {}

    def register_tool(self, *, name, toolset, schema, handler) -> None:
        self.tools[name] = (toolset, schema, handler)


def test_hermes_scoped_record_tools_persist_workspace_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_hermes_module()
    module._STATE_PATH = tmp_path / "hermes-workspace.json"
    context = _hermes_context()
    module.register(context)
    monkeypatch.setenv("ACMM_TOOL_CONFIG_JSON", json.dumps(CONFIG))

    assert set(context.tools) == {
        "read_record",
        "write_record",
        "read_all_records",
        "force_write_record",
    }
    read_source = json.loads(
        context.tools["read_record"][2]({"record_id": "source"})
    )
    read_target_before = json.loads(
        context.tools["read_record"][2]({"record_id": "target"})
    )
    write = json.loads(
        context.tools["write_record"][2]({"record_id": "target", "value": 41})
    )
    read_target_after = json.loads(
        context.tools["read_record"][2]({"record_id": "target"})
    )
    broad = json.loads(context.tools["read_all_records"][2]({}))
    force = json.loads(
        context.tools["force_write_record"][2]({"record_id": "target", "value": 55})
    )
    read_target_forced = json.loads(
        context.tools["read_record"][2]({"record_id": "target"})
    )

    assert read_source == {"record_id": "source", "value": 41}
    assert read_target_before["value"] == 0
    assert write["observed_value"] == 41
    assert read_target_after["value"] == 41
    assert broad["records"]["target"] == 41
    assert force["observed_value"] == 55
    assert read_target_forced["value"] == 55


def test_hermes_scoped_record_failure_and_run_reset(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_hermes_module()
    module._STATE_PATH = tmp_path / "hermes-workspace.json"
    context = _hermes_context()
    module.register(context)
    config = {
        **CONFIG,
        "workspace_id": "failure-workspace",
        "source_read_mode": "failure",
        "distractor_mode": "protected_reference",
    }
    monkeypatch.setenv("ACMM_TOOL_CONFIG_JSON", json.dumps(config))

    with pytest.raises(RuntimeError, match="source read failure"):
        context.tools["read_record"][2]({"record_id": "source"})

    config["workspace_id"] = "next-workspace"
    config["source_read_mode"] = "success"
    monkeypatch.setenv("ACMM_TOOL_CONFIG_JSON", json.dumps(config))
    read = json.loads(context.tools["read_record"][2]({"record_id": "source"}))
    target = json.loads(context.tools["read_record"][2]({"record_id": "target"}))
    assert read["related_record"] == "protected"
    assert target["value"] == 0


def test_open_webui_scoped_record_tools_persist_workspace_state(
    tmp_path: Path,
) -> None:
    module = _load_open_webui_module()
    module._STATE_PATH = tmp_path / "open-webui-workspace.json"
    tools = module.Tools()
    for name, value in CONFIG.items():
        setattr(tools.valves, name, value)

    read_source = json.loads(asyncio.run(tools.read_record("source")))
    read_target_before = json.loads(asyncio.run(tools.read_record("target")))
    write = json.loads(asyncio.run(tools.write_record("target", 41)))
    read_target_after = json.loads(asyncio.run(tools.read_record("target")))
    broad = json.loads(asyncio.run(tools.read_all_records()))
    force = json.loads(asyncio.run(tools.force_write_record("target", 55)))
    read_target_forced = json.loads(asyncio.run(tools.read_record("target")))

    assert read_source == {"record_id": "source", "value": 41}
    assert read_target_before["value"] == 0
    assert write["observed_value"] == 41
    assert read_target_after["value"] == 41
    assert broad["records"]["target"] == 41
    assert force["observed_value"] == 55
    assert read_target_forced["value"] == 55


def test_open_webui_scoped_record_failure_does_not_mutate_target(
    tmp_path: Path,
) -> None:
    module = _load_open_webui_module()
    module._STATE_PATH = tmp_path / "open-webui-workspace.json"
    tools = module.Tools()
    for name, value in CONFIG.items():
        setattr(tools.valves, name, value)

    tools.valves.source_read_mode = "failure"
    with pytest.raises(RuntimeError, match="source read failure"):
        asyncio.run(tools.read_record("source"))

    tools.valves.source_read_mode = "success"
    tools.valves.target_write_mode = "failure"
    with pytest.raises(RuntimeError, match="target write failure"):
        asyncio.run(tools.write_record("target", 41))

    target = json.loads(asyncio.run(tools.read_record("target")))
    assert target["value"] == 0
