from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class run_record_load_error(ValueError):
    """Raised when an ACMM Run Record cannot be loaded as YAML."""


def load_run_record(path: str | Path) -> dict[str, Any]:
    record_path = Path(path)

    try:
        with record_path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as exc:
        raise run_record_load_error(
            f"Could not read Run Record '{record_path}': {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise run_record_load_error(
            f"Could not parse Run Record '{record_path}' as YAML: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise run_record_load_error(
            f"Run Record '{record_path}' must contain a YAML mapping at the top level."
        )

    return data
