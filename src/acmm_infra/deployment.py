from __future__ import annotations

from pathlib import Path
import shutil
from typing import Iterable

from acmm_infra.common import infrastructure_error


def install_default_if_missing(source: Path, destination: Path) -> str:
    if not source.is_file():
        raise infrastructure_error(
            f"Shipped infrastructure default does not exist: '{source}'."
        )

    if destination.exists():
        return "preserved"

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return "created"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        values[key.strip()] = value.strip()

    return values


def write_env(
    path: Path,
    values: dict[str, str],
    *,
    header_lines: Iterable[str],
    line_ending: str = "\n",
) -> None:
    lines = [*header_lines, ""]
    lines.extend(f"{key}={values[key]}" for key in sorted(values))
    content = line_ending.join(lines) + line_ending
    path.write_bytes(content.encode("utf-8"))


def files_equal(left: Path, right: Path) -> bool:
    return (
        left.is_file()
        and right.is_file()
        and left.read_bytes() == right.read_bytes()
    )


def bool_text(value: bool) -> str:
    return "true" if value else "false"
