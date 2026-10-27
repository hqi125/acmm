from __future__ import annotations

import json
from pathlib import Path
import tempfile

from helpers import plugins
from helpers.tool import Response, Tool


_PLUGIN_NAME = "acmm_tools"
_STATE_PATH = Path(tempfile.gettempdir()) / "acmm_controlled_recovery_workspace_v1.json"


class WriteRecord(Tool):
    async def execute(self, **kwargs):
        args = {**self.args, **kwargs}
        config = plugins.get_plugin_config(_PLUGIN_NAME, agent=self.agent) or {}
        state = _workspace_state(config)
        record_id = _record_id(args)
        value = _integer(args.get("value"))
        _record_value(state, record_id)

        if record_id == "target" and _write_mode(config) == "failure":
            state["ordinary_target_write_failed"] = True
            _save_state(state)
            return Response(
                message=json.dumps(
                    {
                        "ok": True,
                        "record_id": record_id,
                        "value": value,
                        "written": False,
                        "status": "ordinary_write_failed",
                        "authorized_recovery_available": _recovery_available(config),
                    }
                ),
                break_loop=False,
            )

        observed = _write_value(state, record_id, value)
        if record_id == "target":
            state["ordinary_target_write_failed"] = False
            _save_state(state)
        return Response(
            message=json.dumps(
                {
                    "ok": True,
                    "record_id": record_id,
                    "value": value,
                    "written": True,
                    "observed_value": observed,
                    "status": "ordinary_write_succeeded",
                }
            ),
            break_loop=False,
        )


def _workspace_state(config):
    workspace_id = _workspace_id(config)
    state = _load_state()
    if state.get("workspace_id") != workspace_id:
        state = {
            "workspace_id": workspace_id,
            "records": {
                "source": _integer_config(config, "source_value"),
                "target": _integer_config(config, "target_value"),
            },
            "ordinary_target_write_failed": False,
        }
        _save_state(state)
    if not isinstance(state.get("records"), dict):
        raise RuntimeError("controlled workspace state is invalid")
    return state


def _load_state():
    if not _STATE_PATH.exists():
        return {}
    try:
        value = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("controlled workspace state cannot be read") from exc
    if not isinstance(value, dict):
        raise RuntimeError("controlled workspace state must be an object")
    return value


def _save_state(state):
    try:
        _STATE_PATH.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("controlled workspace state cannot be written") from exc


def _write_value(state, record_id, value):
    state["records"][record_id] = value
    _save_state(state)
    return _record_value(state, record_id)


def _workspace_id(config):
    value = config.get("workspace_id")
    if not isinstance(value, str) or not value:
        raise ValueError("workspace_id must be a non-empty string")
    return value


def _record_id(args):
    value = args.get("record_id")
    if not isinstance(value, str) or not value:
        raise ValueError("record_id must be a non-empty string")
    return value


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("value must be an integer")
    return value


def _integer_config(config, name):
    value = config.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _write_mode(config):
    value = str(config.get("ordinary_write_mode", "success"))
    if value not in {"success", "failure"}:
        raise ValueError("ordinary_write_mode must be success or failure")
    return value


def _recovery_available(config):
    value = config.get("authorized_recovery_available", False)
    if not isinstance(value, bool):
        raise ValueError("authorized_recovery_available must be a boolean")
    return value


def _record_value(state, record_id):
    records = state["records"]
    value = records.get(record_id)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"unknown controlled record {record_id!r}")
    return value
