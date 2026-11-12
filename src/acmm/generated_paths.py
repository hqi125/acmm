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


__all__ = [
    "default_exports_root",
    "default_generated_root",
    "default_results_root",
]
