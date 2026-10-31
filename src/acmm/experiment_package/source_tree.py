from __future__ import annotations

from collections.abc import Iterator
import hashlib
from pathlib import Path


_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".vscode",
        "__pycache__",
        "exports",
        "results",
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


def validate_package_tree(root: Path) -> None:
    if root.is_symlink():
        raise ValueError("Experiment Package root must not be a symbolic link.")

    for path in sorted(root.rglob("*")):
        if path.is_symlink() and not _is_ignored(path, root):
            relative = path.relative_to(root).as_posix()
            raise ValueError(
                f"Experiment Package managed content must not contain symbolic links: "
                f"'{relative}'."
            )


def is_managed_package_path(path: Path, root: Path) -> bool:
    return not path.is_symlink() and not _is_ignored(path, root)


def iter_managed_package_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and is_managed_package_path(path, root):
            yield path


def package_tree_sha256(root: Path) -> str:
    return paths_sha256(root, iter_managed_package_files(root))


def component_tree_sha256(root: Path) -> str:
    return paths_sha256(root, iter_managed_package_files(root))


def paths_sha256(root: Path, paths: Iterator[Path]) -> str:
    digest = hashlib.sha256()

    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)

    return digest.hexdigest()


def _is_ignored(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)

    if any(part in _IGNORED_DIRECTORY_NAMES for part in relative.parts):
        return True
    if path.name in _IGNORED_FILE_NAMES:
        return True
    return path.suffix in _IGNORED_SUFFIXES
