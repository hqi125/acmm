from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator


EXECUTION_RESOURCE_USAGE_SCHEMA_VERSION = "0.1"
EXECUTION_RESOURCE_USAGE_FILENAME = "execution_resource_usage.yaml"
_SCHEMA_NAME = "execution-resource-usage-v0.1.schema.json"


@dataclass(frozen=True)
class execution_resource_usage_history_item:
    path: Path
    document: Mapping[str, Any]


def validate_execution_resource_usage(document: Mapping[str, Any]) -> None:
    schema_path = Path(__file__).with_name("schemas") / _SCHEMA_NAME
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(dict(document)), key=lambda item: list(item.path))
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(part) for part in error.path) or "<root>"
    raise ValueError(
        f"Execution Resource Usage schema violation at {location}: {error.message}"
    )


def discover_execution_resource_usage_history(
    parent: Path,
    *,
    exclude_session: Path | None = None,
) -> tuple[execution_resource_usage_history_item, ...]:
    root = Path(parent)
    if not root.is_dir():
        return ()
    excluded = exclude_session.resolve() if exclude_session is not None else None
    items: list[execution_resource_usage_history_item] = []
    candidates = [
        session / EXECUTION_RESOURCE_USAGE_FILENAME
        for session in root.glob("execution_*")
        if session.is_dir()
        and (excluded is None or session.resolve() != excluded)
        and (session / EXECUTION_RESOURCE_USAGE_FILENAME).is_file()
    ]
    candidates.sort(key=lambda path: (path.stat().st_mtime, str(path)))
    for path in candidates:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(document, Mapping):
                continue
            validate_execution_resource_usage(document)
        except (OSError, ValueError, yaml.YAMLError):
            continue
        items.append(
            execution_resource_usage_history_item(
                path=path,
                document=dict(document),
            )
        )
    return tuple(items)


def write_execution_resource_usage(path: Path, document: Mapping[str, Any]) -> None:
    value = dict(document)
    validate_execution_resource_usage(value)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(value, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    temporary.replace(target)


__all__ = [
    "EXECUTION_RESOURCE_USAGE_FILENAME",
    "EXECUTION_RESOURCE_USAGE_SCHEMA_VERSION",
    "discover_execution_resource_usage_history",
    "execution_resource_usage_history_item",
    "validate_execution_resource_usage",
    "write_execution_resource_usage",
]
