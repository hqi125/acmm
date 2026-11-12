from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


_SCHEMA_DIRECTORY = Path(__file__).resolve().parent / "schemas"
_SCHEMA_FILES = {
    "0.1": "experiment-package-v0.1.schema.json",
    "0.2": "experiment-package-v0.2.schema.json",
    "0.3": "experiment-package-v0.3.schema.json",
    "0.4": "experiment-package-v0.4.schema.json",
}


def load_package_manifest_schema(schema_version: str) -> dict[str, Any]:
    filename = _SCHEMA_FILES.get(schema_version)
    if filename is None:
        raise ValueError(
            f"Unsupported Experiment Package schema_version '{schema_version}'."
        )

    path = _SCHEMA_DIRECTORY / filename
    try:
        with path.open("r", encoding="utf-8") as file:
            schema = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not load Experiment Package schema '{path}': {exc}"
        ) from exc

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise RuntimeError(
            f"Experiment Package schema '{path}' is invalid: {exc.message}"
        ) from exc

    return schema
