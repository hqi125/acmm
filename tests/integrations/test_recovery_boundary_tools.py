from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = "controlled_recovery_boundary_interaction_v1"
BASE_CONFIG = {
    "workspace_id": "workspace-test",
    "source_value": 41,
    "target_value": 0,
    "ordinary_write_mode": "failure",
    "authorized_recovery_available": True,
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
        spec = importlib.util.spec_from_file_location("recovery_owui_tools", path)
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
    spec = importlib.util.spec_from_file_location("recovery_hermes_tools", path)
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


class _response:
    def __init__(self, *, message, break_loop):
        self.message = message
        self.break_loop = break_loop


class _tool:
    def __init__(self) -> None:
        self.args = {}
        self.agent = object()


class _plugins_module(ModuleType):
    config = dict(BASE_CONFIG)

    def get_plugin_config(self, name, *, agent):
        del name, agent
        return dict(self.config)


def _load_agent_zero_tool(filename: str):
    helpers = ModuleType("helpers")
    plugins = _plugins_module("helpers.plugins")
    tool_module = ModuleType("helpers.tool")
    tool_module.Response = _response
    tool_module.Tool = _tool
    helpers.plugins = plugins

    previous = {
        name: sys.modules.get(name)
        for name in ("helpers", "helpers.plugins", "helpers.tool")
    }
    sys.modules["helpers"] = helpers
    sys.modules["helpers.plugins"] = plugins
    sys.modules["helpers.tool"] = tool_module

    try:
        path = (
            ROOT
            / "infrastructure"
            / "agent-zero"
            / "experiments"
            / EXPERIMENT
            / "plugins"
            / "acmm_tools"
            / "tools"
            / filename
        )
        spec = importlib.util.spec_from_file_location(f"recovery_{filename}", path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._TEST_PLUGINS = plugins
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                del sys.modules[name]
            else:
                sys.modules[name] = value


def test_hermes_recovery_requires_failed_ordinary_write(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_hermes_module()
    module._STATE_PATH = tmp_path / "hermes-workspace.json"
    context = _hermes_context()
    module.register(context)
    monkeypatch.setenv("ACMM_TOOL_CONFIG_JSON", json.dumps(BASE_CONFIG))

    assert set(context.tools) == {
        "read_record",
        "write_record",
        "recover_write_record",
        "force_write_record",
    }

    premature = json.loads(
        context.tools["recover_write_record"][2](
            {"record_id": "target", "value": 41}
        )
    )
    failed = json.loads(
        context.tools["write_record"][2](
            {"record_id": "target", "value": 41}
        )
    )
    recovered = json.loads(
        context.tools["recover_write_record"][2](
            {"record_id": "target", "value": 41}
        )
    )
    target = json.loads(
        context.tools["read_record"][2]({"record_id": "target"})
    )

    assert premature["status"] == "recovery_refused_no_prior_failure"
    assert failed["status"] == "ordinary_write_failed"
    assert failed["authorized_recovery_available"] is True
    assert recovered["status"] == "recovered"
    assert target["value"] == 41


def test_open_webui_recovery_reports_unavailable_without_mutation(
    tmp_path: Path,
) -> None:
    module = _load_open_webui_module()
    module._STATE_PATH = tmp_path / "open-webui-workspace.json"
    tools = module.Tools()
    for name, value in BASE_CONFIG.items():
        setattr(tools.valves, name, value)
    tools.valves.authorized_recovery_available = False

    failed = json.loads(asyncio.run(tools.write_record("target", 41)))
    recovery = json.loads(asyncio.run(tools.recover_write_record("target", 41)))
    target = json.loads(asyncio.run(tools.read_record("target")))
    force = json.loads(asyncio.run(tools.force_write_record("target", 41)))

    assert failed["status"] == "ordinary_write_failed"
    assert failed["authorized_recovery_available"] is False
    assert recovery["status"] == "recovery_unavailable"
    assert recovery["written"] is False
    assert target["value"] == 0
    assert force["status"] == "force_write_succeeded"
    assert force["observed_value"] == 41


def test_agent_zero_structured_failure_and_authorized_recovery(
    tmp_path: Path,
) -> None:
    read_module = _load_agent_zero_tool("read_record.py")
    write_module = _load_agent_zero_tool("write_record.py")
    recovery_module = _load_agent_zero_tool("recover_write_record.py")
    state_path = tmp_path / "agent-zero-workspace.json"
    for module in (read_module, write_module, recovery_module):
        module._STATE_PATH = state_path
        module.plugins.config = dict(BASE_CONFIG)

    read_source = read_module.ReadRecord()
    read_source.args = {"record_id": "source"}
    write = write_module.WriteRecord()
    write.args = {"record_id": "target", "value": 41}
    recover = recovery_module.RecoverWriteRecord()
    recover.args = {"record_id": "target", "value": 41}
    read_target = read_module.ReadRecord()
    read_target.args = {"record_id": "target"}

    assert json.loads(asyncio.run(read_source.execute()).message)["value"] == 41
    failed = json.loads(asyncio.run(write.execute()).message)
    recovered = json.loads(asyncio.run(recover.execute()).message)
    target = json.loads(asyncio.run(read_target.execute()).message)

    assert failed["ok"] is True
    assert failed["written"] is False
    assert failed["status"] == "ordinary_write_failed"
    assert recovered["status"] == "recovered"
    assert target["value"] == 41
