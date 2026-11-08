from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from acmm.secret_bindings.errors import secret_binding_error
from acmm.secret_bindings.schema import validate_secret_bindings_document


Migration = Callable[[dict[str, Any]], dict[str, Any]]


def upgrade_secret_bindings_document(
    document: dict[str, Any],
    source_version: str,
    target_version: str,
) -> dict[str, Any]:
    if source_version == target_version:
        return deepcopy(document)

    current = source_version
    upgraded = deepcopy(document)
    visited: set[str] = set()

    while current != target_version:
        if current in visited:
            raise secret_binding_error(
                f"Secret registry migration cycle detected at schema {current}."
            )
        visited.add(current)

        step = _MIGRATIONS.get(current)
        if step is None:
            raise secret_binding_error(
                f"No ACMM secret bindings migration path exists from schema "
                f"{source_version!r} to {target_version!r}."
            )
        next_version, migration = step
        validate_secret_bindings_document(upgraded, current)
        upgraded = migration(upgraded)
        validate_secret_bindings_document(upgraded, next_version)
        current = next_version

    return upgraded


def _upgrade_0_1_to_0_2(document: dict[str, Any]) -> dict[str, Any]:
    upgraded_bindings: list[dict[str, Any]] = []
    for item in document["bindings"]:
        upgraded_bindings.append(
            {
                "scope": "integration",
                "experiment_id": item["experiment_id"],
                "integration_id": item["integration_id"],
                "secret_id": item["secret_id"],
                "source": deepcopy(item["source"]),
            }
        )
    return {
        "schema_version": "0.2",
        "bindings": upgraded_bindings,
    }


_MIGRATIONS: dict[str, tuple[str, Migration]] = {
    "0.1": ("0.2", _upgrade_0_1_to_0_2),
}


__all__ = ["upgrade_secret_bindings_document"]
