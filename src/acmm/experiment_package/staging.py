from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from acmm.experiment_package.loader import (
    experiment_package_error,
    load_experiment_package,
)
from acmm.experiment_package.model import experiment_package
from acmm.experiment_package.source_tree import iter_managed_package_files


_STAGED_PACKAGE_DIRECTORY = "_experiment_packages"


def stage_experiment_package(
    package: experiment_package,
    runtime_root: str | Path,
    *,
    contract_schema_path: str | Path | None = None,
) -> experiment_package:
    destination = staged_package_root(
        runtime_root,
        package.experiment_id,
        package.package_sha256,
    )

    if destination.exists():
        return open_staged_experiment_package(
            runtime_root,
            package.experiment_id,
            package.package_sha256,
            contract_schema_path=contract_schema_path,
        )

    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix=".staging-", dir=parent))

    try:
        _copy_managed_package(package.root, temporary_root)
        staged = load_experiment_package(
            temporary_root,
            contract_schema_path=contract_schema_path,
        )
        if staged.package_sha256 != package.package_sha256:
            raise experiment_package_error(
                "Experiment Package changed while it was being staged: "
                f"source SHA-256 {package.package_sha256}, staged SHA-256 "
                f"{staged.package_sha256}."
            )

        try:
            temporary_root.rename(destination)
        except FileExistsError:
            shutil.rmtree(temporary_root)

        return open_staged_experiment_package(
            runtime_root,
            package.experiment_id,
            package.package_sha256,
            contract_schema_path=contract_schema_path,
        )
    except BaseException:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
        raise


def install_experiment_package(
    source_root: str | Path,
    runtime_root: str | Path,
    *,
    contract_schema_path: str | Path | None = None,
) -> experiment_package:
    package = load_experiment_package(
        source_root,
        contract_schema_path=contract_schema_path,
    )
    return stage_experiment_package(
        package,
        runtime_root,
        contract_schema_path=contract_schema_path,
    )


def acquire_experiment_package(
    source_root: str | Path,
    runtime_root: str | Path,
    *,
    contract_schema_path: str | Path | None = None,
) -> experiment_package:
    return install_experiment_package(
        source_root,
        runtime_root,
        contract_schema_path=contract_schema_path,
    )


def open_staged_experiment_package(
    runtime_root: str | Path,
    experiment_id: str,
    package_sha256: str,
    *,
    contract_schema_path: str | Path | None = None,
) -> experiment_package:
    root = staged_package_root(runtime_root, experiment_id, package_sha256)
    package = load_experiment_package(
        root,
        contract_schema_path=contract_schema_path,
    )

    if package.experiment_id != experiment_id:
        raise experiment_package_error(
            f"Staged Experiment Package ID is '{package.experiment_id}', expected "
            f"'{experiment_id}'."
        )
    if package.package_sha256 != package_sha256:
        raise experiment_package_error(
            f"Staged Experiment Package SHA-256 is {package.package_sha256}, expected "
            f"{package_sha256}."
        )

    return package


def staged_package_root(
    runtime_root: str | Path,
    experiment_id: str,
    package_sha256: str,
) -> Path:
    runtime = Path(runtime_root).expanduser().resolve()
    _validate_path_component(experiment_id, "experiment_id")
    _validate_sha256(package_sha256)
    return runtime / _STAGED_PACKAGE_DIRECTORY / experiment_id / package_sha256


def _copy_managed_package(source_root: Path, destination_root: Path) -> None:
    for source in iter_managed_package_files(source_root):
        relative = source.relative_to(source_root)
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _validate_path_component(value: str, field: str) -> None:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or len(path.parts) != 1
        or value in {".", ".."}
    ):
        raise experiment_package_error(f"{field} is not a safe path component.")


def _validate_sha256(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise experiment_package_error("package_sha256 must be a lowercase SHA-256 digest.")
