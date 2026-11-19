from __future__ import annotations

import os
from pathlib import Path


_DEFAULT_GENERATED_ROOT = Path("~/acmm_generated")


def default_generated_root() -> Path:
    configured = os.getenv("ACMM_GENERATED_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return _DEFAULT_GENERATED_ROOT.expanduser().resolve()


def default_results_root() -> Path:
    return default_generated_root() / "results"


def default_exports_root() -> Path:
    return default_generated_root() / "exports"


def resolve_results_reference(
    value: str | Path,
    *,
    experiment_id: str | None = None,
) -> Path:
    """Resolve an execution/result reference against the generated-results root.

    Absolute paths and existing explicit relative paths are preserved. Legacy
    ``results/...`` references are re-rooted under ``ACMM_GENERATED_ROOT`` (or
    ``~/acmm_generated``), while experiment-relative and bare execution
    references resolve under the generated ``results`` namespace.
    """
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()

    parts = path.parts
    if parts and parts[0] == "results":
        # ``results/...`` is the canonical generated-results namespace now; do
        # not let a stale repository-local results directory shadow it.
        return (default_generated_root() / path).resolve()

    explicit = path.resolve()
    if explicit.exists():
        return explicit

    if (
        experiment_id
        and len(parts) >= 1
        and parts[0].startswith("execution_")
    ):
        return (default_results_root() / experiment_id / path).resolve()

    return (default_results_root() / path).resolve()


__all__ = [
    "default_exports_root",
    "default_generated_root",
    "default_results_root",
    "resolve_results_reference",
]
