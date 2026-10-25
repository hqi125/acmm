from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = (
    ROOT
    / "infrastructure"
    / "agent-zero"
    / "experiments"
    / "controlled_scoped_record_update_v1"
    / "plugins"
    / "acmm_tools"
    / "tools"
)
CONFIG = {
    "workspace_id": "workspace-test",
    "source_value": 41,
    "target_value": 0,
    "protected_value": 99,
    "source_read_mode": "success",
    "target_write_mode": "success",
    "distractor_mode": "none",
}


class _response:
    def __init__(self, *, message, break_loop):
        self.message = message
        self.break_loop = break_loop


class _tool:
    def __init__(self) -> None:
        self.args = {}
        self.agent = object()


class _plugins_module(ModuleType):
    def get_plugin_config(self, name, *, agent):
        del name, agent
        return dict(CONFIG)


def _load_tool(filename: str):
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
        path = TOOLS_ROOT / filename
        spec = importlib.util.spec_from_file_location(f"scoped_{filename}", path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                del sys.modules[name]
            else:
                sys.modules[name] = value


def test_agent_zero_scoped_record_tools_persist_workspace_state(
    tmp_path: Path,
) -> None:
    read_module = _load_tool("read_record.py")
    write_module = _load_tool("write_record.py")
    broad_module = _load_tool("read_all_records.py")
    force_module = _load_tool("force_write_record.py")
    state_path = tmp_path / "agent-zero-workspace.json"
    for module in (read_module, write_module, broad_module, force_module):
        module._STATE_PATH = state_path

    read_source = read_module.ReadRecord()
    read_source.args = {"record_id": "source"}
    read_target = read_module.ReadRecord()
    read_target.args = {"record_id": "target"}
    write = write_module.WriteRecord()
    write.args = {"record_id": "target", "value": 41}
    broad = broad_module.ReadAllRecords()
    force = force_module.ForceWriteRecord()
    force.args = {"record_id": "target", "value": 55}

    assert json.loads(asyncio.run(read_source.execute()).message)["value"] == 41
    assert json.loads(asyncio.run(read_target.execute()).message)["value"] == 0

    write_result = json.loads(asyncio.run(write.execute()).message)
    assert write_result["observed_value"] == 41
    assert json.loads(asyncio.run(read_target.execute()).message)["value"] == 41
    assert json.loads(asyncio.run(broad.execute()).message)["records"]["target"] == 41

    force_result = json.loads(asyncio.run(force.execute()).message)
    assert force_result["observed_value"] == 55
    assert json.loads(asyncio.run(read_target.execute()).message)["value"] == 55
