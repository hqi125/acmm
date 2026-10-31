from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from acmm.experiment_package import (
    acquire_experiment_package,
    experiment_package_store_error,
    list_installed_experiment_packages,
    resolve_installed_experiment_package,
    uninstall_experiment_package,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = ROOT / "contracts" / "v0_1" / "contract.schema.json"
VALID_CONTRACT = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "magic_number.yaml"
EXPERIMENT_ID = "magic_number_tool_use_v1"


def _write_package(root: Path, readme: str = "Package source.\n") -> None:
    root.mkdir(parents=True)
    shutil.copy2(VALID_CONTRACT, root / "experiment.yaml")
    (root / "experiment-package.yaml").write_text(
        'schema_version: "0.1"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "contract: experiment.yaml\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(readme, encoding="utf-8")


def test_store_lists_and_resolves_unique_installed_package(tmp_path: Path) -> None:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_package(source)
    installed = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    references = list_installed_experiment_packages(runtime_root)
    assert len(references) == 1
    assert references[0].experiment_id == EXPERIMENT_ID
    assert references[0].package_sha256 == installed.package_sha256
    assert references[0].selector == f"{EXPERIMENT_ID}@{installed.package_sha256[:12]}"

    resolved = resolve_installed_experiment_package(
        runtime_root,
        EXPERIMENT_ID,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    assert resolved.package_sha256 == installed.package_sha256


def test_store_requires_revision_selector_when_experiment_is_ambiguous(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_package(source)
    first = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    (source / "README.md").write_text("Second revision.\n", encoding="utf-8")
    second = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    with pytest.raises(experiment_package_store_error, match="ambiguous"):
        resolve_installed_experiment_package(
            runtime_root,
            EXPERIMENT_ID,
            contract_schema_path=CONTRACT_SCHEMA,
        )

    resolved = resolve_installed_experiment_package(
        runtime_root,
        f"{EXPERIMENT_ID}@{second.package_sha256[:12]}",
        contract_schema_path=CONTRACT_SCHEMA,
    )
    assert resolved.package_sha256 == second.package_sha256
    assert resolved.package_sha256 != first.package_sha256


def test_uninstall_removes_only_selected_revision(tmp_path: Path) -> None:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_package(source)
    first = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    (source / "README.md").write_text("Second revision.\n", encoding="utf-8")
    second = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    removed = uninstall_experiment_package(
        runtime_root,
        f"{EXPERIMENT_ID}@{first.package_sha256[:12]}",
        contract_schema_path=CONTRACT_SCHEMA,
    )

    assert removed.package_sha256 == first.package_sha256
    assert not first.root.exists()
    assert second.root.is_dir()
    remaining = list_installed_experiment_packages(runtime_root)
    assert [item.package_sha256 for item in remaining] == [second.package_sha256]


def test_store_rejects_short_or_non_hex_revision_selector(tmp_path: Path) -> None:
    with pytest.raises(experiment_package_store_error, match="8 to 64"):
        resolve_installed_experiment_package(
            tmp_path,
            f"{EXPERIMENT_ID}@abc",
            contract_schema_path=CONTRACT_SCHEMA,
        )

    with pytest.raises(experiment_package_store_error, match="8 to 64"):
        resolve_installed_experiment_package(
            tmp_path,
            f"{EXPERIMENT_ID}@nothex12",
            contract_schema_path=CONTRACT_SCHEMA,
        )



def test_store_rejects_symlinked_revision(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-root"
    experiment_root = runtime_root / "_experiment_packages" / EXPERIMENT_ID
    experiment_root.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (experiment_root / ("0" * 64)).symlink_to(external, target_is_directory=True)

    with pytest.raises(experiment_package_store_error, match="unsafe package revision"):
        list_installed_experiment_packages(runtime_root)
