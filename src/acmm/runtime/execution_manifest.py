from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator


EXPERIMENT_EXECUTION_SCHEMA_VERSION = "0.1"

_SCHEMA_RESOURCES = {
    "0.1": "experiment-execution-v0.1.schema.json",
}


def load_execution_manifest_schema(
    schema_version: str = EXPERIMENT_EXECUTION_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Load one versioned Experiment Execution manifest JSON Schema."""

    try:
        resource_name = _SCHEMA_RESOURCES[schema_version]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported Experiment Execution schema_version '{schema_version}'."
        ) from exc

    resource = files("acmm.runtime").joinpath("schemas", resource_name)
    try:
        schema = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not load Experiment Execution schema '{resource_name}': {exc}"
        ) from exc

    Draft202012Validator.check_schema(schema)
    return schema
