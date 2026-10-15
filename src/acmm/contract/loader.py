from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class contract_load_error(ValueError):
    """Raised when an experiment contract cannot be loaded as YAML."""


def load_contract(path: str | Path) -> dict[str, Any]:
    contract_path = Path(path)

    try:
        with contract_path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as exc:
        raise contract_load_error(
            f"Could not read contract '{contract_path}': {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise contract_load_error(
            f"Could not parse contract '{contract_path}' as YAML: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise contract_load_error(
            f"Contract '{contract_path}' must contain a YAML mapping at the top level."
        )

    return data
