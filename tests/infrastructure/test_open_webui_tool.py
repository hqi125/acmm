import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_IDS = (
    "magic_number_tool_use_v1",
    "controlled_tool_use_multiscenario_v1",
)


def _tool_path(experiment_id: str) -> Path:
    return (
        ROOT
        / "infrastructure"
        / "open-webui"
        / "experiments"
        / experiment_id
        / "tools"
        / "acmm_tools.py"
    )


class _field_definition:
    def __init__(self, default):
        self.default = default


class _base_model:
    def __init__(self, **values) -> None:
        fieldDefaults = {}
        for cls in reversed(type(self).__mro__):
            annotations = getattr(cls, "__annotations__", {})
            for name in annotations:
                default = cls.__dict__.get(name)
                if isinstance(default, _field_definition):
                    default = default.default
                fieldDefaults[name] = default

        unknown = set(values) - set(fieldDefaults)
        if unknown:
            raise TypeError(f"unknown fields: {sorted(unknown)}")

        for name, default in fieldDefaults.items():
            setattr(self, name, values.get(name, default))


def _field(*, default=None, **_kwargs):
    return _field_definition(default)


def _load_tools_class(experiment_id: str):
    # Open WebUI owns the real Pydantic dependency inside its pinned container.
    # ACMM's host-side unit tests stub only the small BaseModel/Field surface the
    # managed Tool needs so the ACMM package does not inherit Open WebUI's
    # internal Python dependency set.
    pydanticStub = ModuleType("pydantic")
    pydanticStub.BaseModel = _base_model
    pydanticStub.Field = _field

    previousPydantic = sys.modules.get("pydantic")
    sys.modules["pydantic"] = pydanticStub

    try:
        spec = importlib.util.spec_from_file_location(
            "acmm_open_webui_tools",
            _tool_path(experiment_id),
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.Tools
    finally:
        if previousPydantic is None:
            del sys.modules["pydantic"]
        else:
            sys.modules["pydantic"] = previousPydantic


@pytest.mark.parametrize("experiment_id", EXPERIMENT_IDS)
def test_acmm_tool_initializes_runtime_valves(experiment_id: str) -> None:
    tools = _load_tools_class(experiment_id)()

    assert tools.valves.magic_number is None
    assert tools.valves.retrieval_mode == "success"
    assert tools.valves.addition_mode == "success"


@pytest.mark.parametrize("experiment_id", EXPERIMENT_IDS)
def test_acmm_tool_uses_controlled_valves(experiment_id: str) -> None:
    tools = _load_tools_class(experiment_id)()
    tools.valves.magic_number = 87

    retrieval = json.loads(asyncio.run(tools.get_magic_number()))
    addition = json.loads(asyncio.run(tools.add_numbers(40, 47)))

    assert retrieval == {"magic_number": 87}
    assert addition == {"result": 87}


@pytest.mark.parametrize("experiment_id", EXPERIMENT_IDS)
def test_acmm_tool_exposes_controlled_failure_modes(experiment_id: str) -> None:
    tools = _load_tools_class(experiment_id)()
    tools.valves.retrieval_mode = "failure"

    with pytest.raises(RuntimeError, match="retrieval failure"):
        asyncio.run(tools.get_magic_number())
