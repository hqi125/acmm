from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path


_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)
_IGNORED_FILE_NAMES = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
    }
)
_IGNORED_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
    }
)


def iter_managed_source_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not is_generated_source_artifact(path, root):
            yield path


def is_generated_source_artifact(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in _IGNORED_DIRECTORY_NAMES for part in relative.parts[:-1]):
        return True
    if path.name in _IGNORED_FILE_NAMES:
        return True
    return path.suffix in _IGNORED_SUFFIXES


def copytree_ignore_generated(
    _directory: str,
    names: Sequence[str],
) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(name)
        if name in _IGNORED_DIRECTORY_NAMES:
            ignored.add(name)
        elif name in _IGNORED_FILE_NAMES:
            ignored.add(name)
        elif path.suffix in _IGNORED_SUFFIXES:
            ignored.add(name)
    return ignored
