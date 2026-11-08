from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile
from typing import Any, TypeAlias

import yaml

from acmm.secret_bindings.errors import (
    secret_binding_error,
    secret_binding_version_error,
)
from acmm.secret_bindings.migrations import upgrade_secret_bindings_document
from acmm.secret_bindings.schema import (
    CURRENT_SECRET_BINDINGS_SCHEMA_VERSION,
    declared_secret_bindings_schema_version,
    validate_secret_bindings_document,
)


_SECRET_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_DEFAULT_BINDINGS_FILE = Path("~/.config/acmm/secret-bindings.yaml")
_DEFAULT_SECRET_DIRECTORY = Path("~/.config/acmm/secrets")


def _require_identifier(label: str, value: str) -> None:
    if _SECRET_ID.fullmatch(value) is None:
        raise secret_binding_error(
            f"{label} must match [a-z0-9][a-z0-9_-]*: {value!r}."
        )


@dataclass(frozen=True)
class core_secret_binding_key:
    secret_id: str

    def __post_init__(self) -> None:
        _require_identifier("secret_id", self.secret_id)


@dataclass(frozen=True)
class experiment_secret_binding_key:
    experiment_id: str
    secret_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise secret_binding_error("experiment_id must be non-empty text.")
        _require_identifier("secret_id", self.secret_id)


@dataclass(frozen=True)
class secret_binding_key:
    """Integration-scoped secret binding key retained for API compatibility."""

    experiment_id: str
    integration_id: str
    secret_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise secret_binding_error("experiment_id must be non-empty text.")
        _require_identifier("integration_id", self.integration_id)
        _require_identifier("secret_id", self.secret_id)


secret_binding_key_type: TypeAlias = (
    core_secret_binding_key | experiment_secret_binding_key | secret_binding_key
)


@dataclass(frozen=True)
class secret_binding:
    key: secret_binding_key_type
    source_kind: str
    source_path: Path


@dataclass(frozen=True)
class secret_store_initialization:
    bindings_path: Path
    secret_directory: Path
    registry_created: bool
    registry_schema_version: str
    registry_is_current: bool


@dataclass(frozen=True)
class secret_registry_upgrade:
    bindings_path: Path
    source_version: str
    target_version: str
    changed: bool


def default_secret_bindings_path() -> Path:
    override = os.environ.get("ACMM_SECRET_BINDINGS_FILE")
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_BINDINGS_FILE.expanduser().resolve()


def default_secret_directory_path() -> Path:
    override = os.environ.get("ACMM_SECRET_DIRECTORY")
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_SECRET_DIRECTORY.expanduser().resolve()


def initialize_secret_store(
    *,
    bindings_path: str | Path | None = None,
    secret_directory: str | Path | None = None,
) -> secret_store_initialization:
    registry = _bindings_path(bindings_path)
    directory = _secret_directory_path(secret_directory)

    _ensure_directory(registry.parent)
    _ensure_secret_directory(directory)

    created = False
    if registry.exists():
        document = _load_declared_document(registry)
        version = declared_secret_bindings_schema_version(document)
        _secure_registry_permissions(registry)
    else:
        document = _empty_current_document()
        _write_current_document(registry, document)
        version = CURRENT_SECRET_BINDINGS_SCHEMA_VERSION
        created = True

    return secret_store_initialization(
        bindings_path=registry,
        secret_directory=directory,
        registry_created=created,
        registry_schema_version=version,
        registry_is_current=(version == CURRENT_SECRET_BINDINGS_SCHEMA_VERSION),
    )


def validate_secret_bindings_registry(
    *,
    bindings_path: str | Path | None = None,
) -> str:
    destination = _bindings_path(bindings_path)
    if not destination.exists():
        raise secret_binding_error(
            f"ACMM secret bindings registry does not exist at '{destination}'; "
            "run 'acmm secret init' first."
        )

    document = _load_current_document(destination)
    failures: list[str] = []
    for item in _binding_records(document):
        key = _record_key(item)
        source_path = Path(item["source"]["path"]).expanduser().resolve()
        try:
            _read_secret_value(source_path)
        except secret_binding_error as exc:
            failures.append(f"{_key_description(key)}: {exc}")

    if failures:
        details = "\n".join(f"  - {failure}" for failure in failures)
        raise secret_binding_error(
            "ACMM secret bindings registry contains unusable secret source(s):\n"
            + details
        )

    return CURRENT_SECRET_BINDINGS_SCHEMA_VERSION


def upgrade_secret_bindings_registry(
    *,
    bindings_path: str | Path | None = None,
) -> secret_registry_upgrade:
    destination = _bindings_path(bindings_path)
    if not destination.exists():
        raise secret_binding_error(
            f"ACMM secret bindings registry does not exist at '{destination}'; "
            "run 'acmm secret init' first."
        )

    document = _load_declared_document(destination)
    source_version = declared_secret_bindings_schema_version(document)
    target_version = CURRENT_SECRET_BINDINGS_SCHEMA_VERSION

    if source_version == target_version:
        _validate_current_document_semantics(document)
        return secret_registry_upgrade(
            bindings_path=destination,
            source_version=source_version,
            target_version=target_version,
            changed=False,
        )

    upgraded = upgrade_secret_bindings_document(
        document,
        source_version,
        target_version,
    )
    _validate_current_document_semantics(upgraded)
    _write_current_document(destination, upgraded)
    return secret_registry_upgrade(
        bindings_path=destination,
        source_version=source_version,
        target_version=target_version,
        changed=True,
    )


def bind_secret_file(
    key: secret_binding_key_type,
    source_path: str | Path,
    *,
    bindings_path: str | Path | None = None,
    replace: bool = False,
) -> secret_binding:
    path = Path(source_path).expanduser().resolve()
    _validate_secret_file(path)

    destination = _bindings_path(bindings_path)
    document = _load_current_document(destination)
    bindings = _binding_records(document)
    record = _binding_record(key, path)

    for index, existing in enumerate(bindings):
        if _record_key(existing) != key:
            continue
        existing_path = Path(existing["source"]["path"]).expanduser().resolve()
        if existing_path == path:
            return secret_binding(key=key, source_kind="file", source_path=path)
        if not replace:
            raise secret_binding_error(
                f"A local secret binding already exists for {_key_description(key)}: "
                f"file:{existing_path}. Use --replace to change its source."
            )
        bindings[index] = record
        break
    else:
        bindings.append(record)

    bindings.sort(key=lambda item: _key_sort_tuple(_record_key(item)))
    _write_current_document(destination, document)
    return secret_binding(key=key, source_kind="file", source_path=path)


def unbind_secret(
    key: secret_binding_key_type,
    *,
    bindings_path: str | Path | None = None,
) -> bool:
    destination = _bindings_path(bindings_path)
    document = _load_current_document(destination)
    bindings = _binding_records(document)
    original = len(bindings)
    bindings[:] = [item for item in bindings if _record_key(item) != key]
    changed = len(bindings) != original
    if changed:
        _write_current_document(destination, document)
    return changed


def list_secret_bindings(
    *,
    bindings_path: str | Path | None = None,
) -> tuple[secret_binding, ...]:
    destination = _bindings_path(bindings_path)
    document = _load_current_document(destination)
    result: list[secret_binding] = []
    for item in _binding_records(document):
        key = _record_key(item)
        source = item["source"]
        result.append(
            secret_binding(
                key=key,
                source_kind="file",
                source_path=Path(source["path"]).expanduser().resolve(),
            )
        )
    return tuple(result)


def resolve_secret(
    key: secret_binding_key_type,
    *,
    bindings_path: str | Path | None = None,
) -> str:
    matches = [
        item
        for item in list_secret_bindings(bindings_path=bindings_path)
        if item.key == key
    ]
    if not matches:
        raise secret_binding_error(
            f"No local secret binding exists for {_key_description(key)}."
        )
    if len(matches) != 1:
        raise secret_binding_error(
            f"Multiple local secret bindings exist for {_key_description(key)}."
        )

    binding = matches[0]
    return _read_secret_value(binding.source_path)


def has_secret_binding(
    key: secret_binding_key_type,
    *,
    bindings_path: str | Path | None = None,
) -> bool:
    return any(
        item.key == key
        for item in list_secret_bindings(bindings_path=bindings_path)
    )


def secret_binding_label(key: secret_binding_key_type) -> str:
    return _key_description(key)


def _bindings_path(value: str | Path | None) -> Path:
    if value is None:
        return default_secret_bindings_path()
    return Path(value).expanduser().resolve()


def _secret_directory_path(value: str | Path | None) -> Path:
    if value is None:
        return default_secret_directory_path()
    return Path(value).expanduser().resolve()


def _empty_current_document() -> dict[str, Any]:
    return {
        "schema_version": CURRENT_SECRET_BINDINGS_SCHEMA_VERSION,
        "bindings": [],
    }


def _load_yaml_document(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise secret_binding_error(
            f"Could not load ACMM secret bindings '{path}': {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise secret_binding_error(
            "ACMM secret bindings registry root must be a mapping."
        )
    return raw


def _load_declared_document(path: Path) -> dict[str, Any]:
    document = _load_yaml_document(path)
    version = declared_secret_bindings_schema_version(document)
    validate_secret_bindings_document(document, version)
    return document


def _load_current_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_current_document()

    document = _load_declared_document(path)
    version = declared_secret_bindings_schema_version(document)
    if version != CURRENT_SECRET_BINDINGS_SCHEMA_VERSION:
        raise secret_binding_version_error(
            f"ACMM secret bindings registry schema_version {version!r} does not match "
            f"the Core-required version {CURRENT_SECRET_BINDINGS_SCHEMA_VERSION!r}; "
            "run 'acmm secret upgrade'."
        )
    _validate_current_document_semantics(document)
    return document


def _validate_current_document_semantics(document: dict[str, Any]) -> None:
    bindings = _binding_records(document)
    seen: set[secret_binding_key_type] = set()
    for item in bindings:
        key = _record_key(item)
        if key in seen:
            raise secret_binding_error(
                f"Duplicate ACMM secret binding key: {_key_description(key)}."
            )
        seen.add(key)


def _binding_records(document: dict[str, Any]) -> list[dict[str, Any]]:
    values = document["bindings"]
    return values


def _record_key(item: dict[str, Any]) -> secret_binding_key_type:
    scope = item["scope"]
    if scope == "core":
        return core_secret_binding_key(secret_id=item["secret_id"])
    if scope == "experiment":
        return experiment_secret_binding_key(
            experiment_id=item["experiment_id"],
            secret_id=item["secret_id"],
        )
    if scope == "integration":
        return secret_binding_key(
            experiment_id=item["experiment_id"],
            integration_id=item["integration_id"],
            secret_id=item["secret_id"],
        )
    raise secret_binding_error(f"Unsupported secret binding scope: {scope!r}.")


def _binding_record(
    key: secret_binding_key_type,
    path: Path,
) -> dict[str, Any]:
    source = {"kind": "file", "path": str(path)}
    if isinstance(key, core_secret_binding_key):
        return {
            "scope": "core",
            "secret_id": key.secret_id,
            "source": source,
        }
    if isinstance(key, experiment_secret_binding_key):
        return {
            "scope": "experiment",
            "experiment_id": key.experiment_id,
            "secret_id": key.secret_id,
            "source": source,
        }
    return {
        "scope": "integration",
        "experiment_id": key.experiment_id,
        "integration_id": key.integration_id,
        "secret_id": key.secret_id,
        "source": source,
    }


def _key_sort_tuple(key: secret_binding_key_type) -> tuple[str, ...]:
    if isinstance(key, core_secret_binding_key):
        return ("core", key.secret_id)
    if isinstance(key, experiment_secret_binding_key):
        return ("experiment", key.experiment_id, key.secret_id)
    return (
        "integration",
        key.experiment_id,
        key.integration_id,
        key.secret_id,
    )


def _key_description(key: secret_binding_key_type) -> str:
    if isinstance(key, core_secret_binding_key):
        return f"core:{key.secret_id}"
    if isinstance(key, experiment_secret_binding_key):
        return f"experiment:{key.experiment_id}:{key.secret_id}"
    return (
        f"integration:{key.experiment_id}:{key.integration_id}:"
        f"{key.secret_id}"
    )


def _write_current_document(path: Path, document: dict[str, Any]) -> None:
    document["schema_version"] = CURRENT_SECRET_BINDINGS_SCHEMA_VERSION
    validate_secret_bindings_document(
        document,
        CURRENT_SECRET_BINDINGS_SCHEMA_VERSION,
    )
    _validate_current_document_semantics(document)
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=False)

    _ensure_directory(path.parent)

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as file:
            file.write(text)
            temporary = Path(file.name)
        if os.name == "posix":
            temporary.chmod(0o600)
        os.replace(temporary, path)
        temporary = None
        _secure_registry_permissions(path)
    except OSError as exc:
        raise secret_binding_error(
            f"Could not write ACMM secret bindings '{path}': {exc}"
        ) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _ensure_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise secret_binding_error(
            f"Could not create ACMM secret registry directory '{path}': {exc}"
        ) from exc


def _ensure_secret_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            path.chmod(0o700)
    except OSError as exc:
        raise secret_binding_error(
            f"Could not initialize ACMM secret directory '{path}': {exc}"
        ) from exc


def _secure_registry_permissions(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise secret_binding_error(
            f"Could not secure ACMM secret bindings registry '{path}': {exc}"
        ) from exc


def _read_secret_value(path: Path) -> str:
    _validate_secret_file(path)
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise secret_binding_error(
            f"Could not read secret source '{path}' as UTF-8 text: {exc}"
        ) from exc

    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if not value:
        raise secret_binding_error(f"Secret source '{path}' is empty.")
    return value


def _validate_secret_file(path: Path) -> None:
    if not path.is_file():
        raise secret_binding_error(f"Secret source is not a file: '{path}'.")
    if os.name != "posix":
        return
    try:
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        raise secret_binding_error(
            f"Could not inspect secret source '{path}': {exc}"
        ) from exc
    if mode & 0o077:
        raise secret_binding_error(
            f"Secret source '{path}' must not be readable or writable by group/other "
            f"(observed mode {mode:03o}; use chmod 600)."
        )


__all__ = [
    "bind_secret_file",
    "core_secret_binding_key",
    "default_secret_bindings_path",
    "default_secret_directory_path",
    "experiment_secret_binding_key",
    "has_secret_binding",
    "initialize_secret_store",
    "list_secret_bindings",
    "resolve_secret",
    "secret_binding",
    "secret_binding_key",
    "secret_binding_key_type",
    "secret_binding_label",
    "secret_registry_upgrade",
    "secret_store_initialization",
    "unbind_secret",
    "upgrade_secret_bindings_registry",
    "validate_secret_bindings_registry",
]
