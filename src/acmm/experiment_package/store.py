from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil

from acmm.experiment_package.loader import experiment_package_error
from acmm.experiment_package.model import experiment_package
from acmm.experiment_package.staging import open_staged_experiment_package


_STAGED_PACKAGE_DIRECTORY = "_experiment_packages"
_EXPERIMENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SHA256_PREFIX_PATTERN = re.compile(r"^[0-9a-f]{8,64}$")


class experiment_package_store_error(ValueError):
    """Raised when an installed Experiment Package cannot be resolved safely."""


@dataclass(frozen=True)
class installed_experiment_package:
    experiment_id: str
    package_sha256: str
    root: Path

    @property
    def selector(self) -> str:
        return f"{self.experiment_id}@{self.package_sha256[:12]}"


def installed_package_store_root(runtime_root: str | Path) -> Path:
    runtime = Path(runtime_root).expanduser().resolve()
    return runtime / _STAGED_PACKAGE_DIRECTORY


def list_installed_experiment_packages(
    runtime_root: str | Path,
) -> tuple[installed_experiment_package, ...]:
    store_root = installed_package_store_root(runtime_root)
    if not store_root.exists():
        return ()
    if store_root.is_symlink() or not store_root.is_dir():
        raise experiment_package_store_error(
            f"Experiment Package store is not a safe directory: {store_root}"
        )

    installed: list[installed_experiment_package] = []
    for experiment_root in sorted(store_root.iterdir(), key=lambda item: item.name):
        if experiment_root.name.startswith("."):
            continue
        if experiment_root.is_symlink() or not experiment_root.is_dir():
            raise experiment_package_store_error(
                "Experiment Package store contains an unsafe experiment entry: "
                f"{experiment_root}"
            )
        if _EXPERIMENT_ID_PATTERN.fullmatch(experiment_root.name) is None:
            raise experiment_package_store_error(
                "Experiment Package store contains an invalid experiment directory: "
                f"{experiment_root.name}"
            )

        for package_root in sorted(experiment_root.iterdir(), key=lambda item: item.name):
            if package_root.name.startswith("."):
                continue
            if package_root.is_symlink() or not package_root.is_dir():
                raise experiment_package_store_error(
                    "Experiment Package store contains an unsafe package revision entry: "
                    f"{package_root}"
                )
            if _SHA256_PATTERN.fullmatch(package_root.name) is None:
                raise experiment_package_store_error(
                    "Experiment Package store contains an invalid package revision directory: "
                    f"{package_root}"
                )
            installed.append(
                installed_experiment_package(
                    experiment_id=experiment_root.name,
                    package_sha256=package_root.name,
                    root=package_root.resolve(),
                )
            )

    return tuple(installed)


def resolve_installed_experiment_package(
    runtime_root: str | Path,
    selector: str,
    *,
    contract_schema_path: str | Path | None = None,
) -> experiment_package:
    experiment_id, package_prefix = _parse_package_selector(selector)
    candidates = [
        item
        for item in list_installed_experiment_packages(runtime_root)
        if item.experiment_id == experiment_id
        and (
            package_prefix is None
            or item.package_sha256.startswith(package_prefix)
        )
    ]

    if not candidates:
        if package_prefix is None:
            detail = f"experiment '{experiment_id}'"
        else:
            detail = f"selector '{selector}'"
        raise experiment_package_store_error(
            f"No installed Experiment Package matches {detail}."
        )

    if len(candidates) != 1:
        options = ", ".join(item.selector for item in candidates)
        raise experiment_package_store_error(
            f"Experiment Package selector '{selector}' is ambiguous; choose one of: "
            f"{options}."
        )

    selected = candidates[0]
    try:
        return open_staged_experiment_package(
            runtime_root,
            selected.experiment_id,
            selected.package_sha256,
            contract_schema_path=contract_schema_path,
        )
    except experiment_package_error as exc:
        raise experiment_package_store_error(str(exc)) from exc


def uninstall_experiment_package(
    runtime_root: str | Path,
    selector: str,
    *,
    contract_schema_path: str | Path | None = None,
) -> installed_experiment_package:
    package = resolve_installed_experiment_package(
        runtime_root,
        selector,
        contract_schema_path=contract_schema_path,
    )
    installed = installed_experiment_package(
        experiment_id=package.experiment_id,
        package_sha256=package.package_sha256,
        root=package.root,
    )

    shutil.rmtree(package.root)
    experiment_root = package.root.parent
    if experiment_root.exists() and not any(experiment_root.iterdir()):
        experiment_root.rmdir()

    store_root = installed_package_store_root(runtime_root)
    if store_root.exists() and not any(store_root.iterdir()):
        store_root.rmdir()

    return installed


def _parse_package_selector(selector: str) -> tuple[str, str | None]:
    if selector.count("@") > 1:
        raise experiment_package_store_error(
            "Experiment Package selector must use EXPERIMENT_ID or "
            "EXPERIMENT_ID@SHA_PREFIX."
        )

    if "@" in selector:
        experiment_id, package_prefix = selector.split("@", 1)
    else:
        experiment_id = selector
        package_prefix = None

    if _EXPERIMENT_ID_PATTERN.fullmatch(experiment_id) is None:
        raise experiment_package_store_error(
            f"Invalid Experiment Package experiment ID in selector: '{experiment_id}'."
        )

    if (
        package_prefix is not None
        and _SHA256_PREFIX_PATTERN.fullmatch(package_prefix) is None
    ):
        raise experiment_package_store_error(
            "Experiment Package SHA prefix must contain 8 to 64 lowercase hexadecimal "
            "characters."
        )

    return experiment_id, package_prefix
