from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class profile_error(ValueError):
    """Raised when an ACMM infrastructure profile is invalid."""


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_runtime_root() -> Path:
    configured = os.getenv("ACMM_RUNTIME_ROOT")

    if configured:
        return Path(configured).expanduser()

    return Path.home() / "acmm_runtime"


def _nonempty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise profile_error(f"{path} must be a non-empty string.")

    return value.strip()


def _safe_relative_path(value: Any, path: str) -> Path:
    text = _nonempty_string(value, path)
    parsed = Path(text)

    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise profile_error(f"{path} must be a safe relative path.")

    return parsed


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise profile_error(f"{path} must be a boolean.")

    return value


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise profile_error(f"{path} must be a list.")

    items = tuple(
        _nonempty_string(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    )

    if len(items) != len(set(items)):
        raise profile_error(f"{path} must not contain duplicates.")

    return items
