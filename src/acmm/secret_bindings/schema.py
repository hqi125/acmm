from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from acmm.secret_bindings.errors import secret_binding_error


CURRENT_SECRET_BINDINGS_SCHEMA_VERSION = "0.2"
_SCHEMA_FILES = {
    "0.1": "secret-bindings-v0.1.schema.json",
    "0.2": "secret-bindings-v0.2.schema.json",
}


def supported_secret_bindings_schema_versions() -> tuple[str, ...]:
    return tuple(_SCHEMA_FILES)


def secret_bindings_schema_path(schema_version: str) -> Path:
    filename = _SCHEMA_FILES.get(schema_version)
    if filename is None:
        supported = ", ".join(supported_secret_bindings_schema_versions())
        raise secret_binding_error(
            f"Unsupported ACMM secret bindings schema_version {schema_version!r}; "
            f"supported versions: {supported}."
        )
    return Path(__file__).resolve().parent / "schemas" / filename


def load_secret_bindings_schema(schema_version: str) -> dict[str, Any]:
    path = secret_bindings_schema_path(schema_version)
    try:
        with path.open("r", encoding="utf-8") as file:
            schema = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise secret_binding_error(
            f"Could not load ACMM secret bindings schema '{path}': {exc}"
        ) from exc
    Draft202012Validator.check_schema(schema)
    return schema


def declared_secret_bindings_schema_version(document: Mapping[str, Any]) -> str:
    value = document.get("schema_version")
    if not isinstance(value, str) or not value.strip():
        raise secret_binding_error(
            "ACMM secret bindings registry requires a non-empty string schema_version."
        )
    return value


def validate_secret_bindings_document(
    document: Mapping[str, Any],
    schema_version: str,
) -> None:
    schema = load_secret_bindings_schema(schema_version)
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    path = _format_path(error.absolute_path)
    raise secret_binding_error(
        f"ACMM secret bindings schema {schema_version} violation at '{path}': "
        f"{error.message}"
    )


def _format_path(parts) -> str:
    values = list(parts)
    if not values:
        return "$"
    result = "$"
    for value in values:
        if isinstance(value, int):
            result += f"[{value}]"
        else:
            result += f".{value}"
    return result


__all__ = [
    "CURRENT_SECRET_BINDINGS_SCHEMA_VERSION",
    "declared_secret_bindings_schema_version",
    "load_secret_bindings_schema",
    "secret_bindings_schema_path",
    "supported_secret_bindings_schema_versions",
    "validate_secret_bindings_document",
]
