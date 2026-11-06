from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any

import yaml


_SECRET_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SCHEMA_VERSION = "0.1"
_DEFAULT_BINDINGS_FILE = Path("~/.config/acmm/secret-bindings.yaml")


class secret_binding_error(ValueError):
    """Raised when a local ACMM secret binding cannot be resolved safely."""


@dataclass(frozen=True)
class secret_binding_key:
    experiment_id: str
    integration_id: str
    secret_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise secret_binding_error("experiment_id must be non-empty text.")
        for label, value in (
            ("integration_id", self.integration_id),
            ("secret_id", self.secret_id),
        ):
            if _SECRET_ID.fullmatch(value) is None:
                raise secret_binding_error(
                    f"{label} must match [a-z0-9][a-z0-9_-]*: {value!r}."
                )


@dataclass(frozen=True)
class secret_binding:
    key: secret_binding_key
    source_kind: str
    source_path: Path


def default_secret_bindings_path() -> Path:
    override = os.environ.get("ACMM_SECRET_BINDINGS_FILE")
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_BINDINGS_FILE.expanduser().resolve()


def bind_secret_file(
    key: secret_binding_key,
    source_path: str | Path,
    *,
    bindings_path: str | Path | None = None,
) -> secret_binding:
    path = Path(source_path).expanduser().resolve()
    _validate_secret_file(path)

    destination = _bindings_path(bindings_path)
    document = _load_document(destination)
    bindings = _binding_records(document)
    record = _binding_record(key, path)

    replaced = False
    for index, existing in enumerate(bindings):
        if _record_key(existing) == key:
            bindings[index] = record
            replaced = True
            break
    if not replaced:
        bindings.append(record)

    bindings.sort(
        key=lambda item: (
            str(item["experiment_id"]),
            str(item["integration_id"]),
            str(item["secret_id"]),
        )
    )
    _write_document(destination, document)
    return secret_binding(key=key, source_kind="file", source_path=path)


def unbind_secret(
    key: secret_binding_key,
    *,
    bindings_path: str | Path | None = None,
) -> bool:
    destination = _bindings_path(bindings_path)
    document = _load_document(destination)
    bindings = _binding_records(document)
    original = len(bindings)
    bindings[:] = [item for item in bindings if _record_key(item) != key]
    changed = len(bindings) != original
    if changed:
        _write_document(destination, document)
    return changed


def list_secret_bindings(
    *,
    bindings_path: str | Path | None = None,
) -> tuple[secret_binding, ...]:
    destination = _bindings_path(bindings_path)
    document = _load_document(destination)
    result: list[secret_binding] = []
    for item in _binding_records(document):
        key = _record_key(item)
        source = item.get("source")
        if not isinstance(source, dict) or source.get("kind") != "file":
            raise secret_binding_error(
                f"Unsupported secret binding source for {key}: {source!r}."
            )
        value = source.get("path")
        if not isinstance(value, str) or not value.strip():
            raise secret_binding_error(f"Secret binding path is invalid for {key}.")
        result.append(
            secret_binding(
                key=key,
                source_kind="file",
                source_path=Path(value).expanduser().resolve(),
            )
        )
    return tuple(result)


def resolve_secret(
    key: secret_binding_key,
    *,
    bindings_path: str | Path | None = None,
) -> str:
    matches = [item for item in list_secret_bindings(bindings_path=bindings_path) if item.key == key]
    if not matches:
        raise secret_binding_error(
            "No local secret binding exists for "
            f"{key.experiment_id}:{key.integration_id}:{key.secret_id}."
        )
    if len(matches) != 1:
        raise secret_binding_error(
            "Multiple local secret bindings exist for "
            f"{key.experiment_id}:{key.integration_id}:{key.secret_id}."
        )

    binding = matches[0]
    _validate_secret_file(binding.source_path)
    try:
        value = binding.source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise secret_binding_error(
            f"Could not read secret source '{binding.source_path}': {exc}"
        ) from exc

    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if not value:
        raise secret_binding_error(
            f"Secret source '{binding.source_path}' is empty."
        )
    return value


def has_secret_binding(
    key: secret_binding_key,
    *,
    bindings_path: str | Path | None = None,
) -> bool:
    return any(item.key == key for item in list_secret_bindings(bindings_path=bindings_path))


def _bindings_path(value: str | Path | None) -> Path:
    if value is None:
        return default_secret_bindings_path()
    return Path(value).expanduser().resolve()


def _load_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": _SCHEMA_VERSION, "bindings": []}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise secret_binding_error(
            f"Could not load ACMM secret bindings '{path}': {exc}"
        ) from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise secret_binding_error("ACMM secret bindings root must be a mapping.")
    schema = raw.get("schema_version")
    if schema != _SCHEMA_VERSION:
        raise secret_binding_error(
            f"Unsupported ACMM secret bindings schema version: {schema!r}."
        )
    raw.setdefault("bindings", [])
    _binding_records(raw)
    return raw


def _binding_records(document: dict[str, Any]) -> list[dict[str, Any]]:
    values = document.get("bindings")
    if not isinstance(values, list):
        raise secret_binding_error("ACMM secret bindings 'bindings' must be a sequence.")
    for item in values:
        if not isinstance(item, dict):
            raise secret_binding_error("Each ACMM secret binding must be a mapping.")
        _record_key(item)
    return values


def _record_key(item: dict[str, Any]) -> secret_binding_key:
    try:
        return secret_binding_key(
            experiment_id=str(item["experiment_id"]),
            integration_id=str(item["integration_id"]),
            secret_id=str(item["secret_id"]),
        )
    except KeyError as exc:
        raise secret_binding_error(
            f"Secret binding is missing required field {exc.args[0]!r}."
        ) from exc


def _binding_record(key: secret_binding_key, path: Path) -> dict[str, Any]:
    return {
        "experiment_id": key.experiment_id,
        "integration_id": key.integration_id,
        "secret_id": key.secret_id,
        "source": {"kind": "file", "path": str(path)},
    }


def _write_document(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=False)
    try:
        path.write_text(text, encoding="utf-8", newline="\n")
        if os.name == "posix":
            path.chmod(0o600)
    except OSError as exc:
        raise secret_binding_error(
            f"Could not write ACMM secret bindings '{path}': {exc}"
        ) from exc


def _validate_secret_file(path: Path) -> None:
    if not path.is_file():
        raise secret_binding_error(f"Secret source is not a file: '{path}'.")
    if os.name != "posix":
        return
    try:
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        raise secret_binding_error(f"Could not inspect secret source '{path}': {exc}") from exc
    if mode & 0o077:
        raise secret_binding_error(
            f"Secret source '{path}' must not be readable or writable by group/other "
            f"(observed mode {mode:03o}; use chmod 600)."
        )


__all__ = [
    "bind_secret_file",
    "default_secret_bindings_path",
    "has_secret_binding",
    "list_secret_bindings",
    "resolve_secret",
    "secret_binding",
    "secret_binding_error",
    "secret_binding_key",
    "unbind_secret",
]
