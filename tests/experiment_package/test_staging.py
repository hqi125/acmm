from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from acmm.experiment_package import (
    acquire_experiment_package,
    experiment_package_error,
    load_experiment_package,
    open_staged_experiment_package,
    stage_experiment_package,
    staged_package_root,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = ROOT / "contracts" / "v0_1" / "contract.schema.json"
VALID_CONTRACT = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "package_fixture.yaml"
EXPERIMENT_ID = "package_fixture_v1"


def _write_minimal_package(root: Path) -> None:
    root.mkdir(parents=True)
    shutil.copy2(VALID_CONTRACT, root / "experiment.yaml")
    (root / "experiment-package.yaml").write_text(
        'schema_version: "0.1"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "contract: experiment.yaml\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("Package source.\n", encoding="utf-8")


def test_acquire_stages_digest_identified_snapshot(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_minimal_package(source_root)

    source = load_experiment_package(
        source_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    staged = stage_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    expected_root = (
        runtime_root.resolve()
        / "_experiment_packages"
        / EXPERIMENT_ID
        / source.package_sha256
    )
    assert staged.root == expected_root
    assert staged.package_sha256 == source.package_sha256
    assert (expected_root / "README.md").read_text(encoding="utf-8") == "Package source.\n"


def test_staged_package_reopens_without_source_directory(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_minimal_package(source_root)

    staged = acquire_experiment_package(
        source_root,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    package_sha256 = staged.package_sha256
    shutil.rmtree(source_root)

    reopened = open_staged_experiment_package(
        runtime_root,
        EXPERIMENT_ID,
        package_sha256,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    assert reopened.root == staged.root
    assert reopened.package_sha256 == package_sha256


def test_reacquiring_same_package_reuses_verified_snapshot(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_minimal_package(source_root)

    first = acquire_experiment_package(
        source_root,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    second = acquire_experiment_package(
        source_root,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    assert second.root == first.root
    assert second.package_sha256 == first.package_sha256


def test_changed_source_stages_as_distinct_snapshot(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_minimal_package(source_root)

    first = acquire_experiment_package(
        source_root,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    (source_root / "README.md").write_text("Changed package source.\n", encoding="utf-8")
    second = acquire_experiment_package(
        source_root,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    assert second.package_sha256 != first.package_sha256
    assert second.root != first.root
    assert first.root.is_dir()
    assert second.root.is_dir()


def test_staging_excludes_generated_artifacts(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_minimal_package(source_root)
    (source_root / ".git").mkdir()
    (source_root / ".git" / "index").write_bytes(b"metadata")
    (source_root / "results").mkdir()
    (source_root / "results" / "run.yaml").write_text("result: 1\n")

    staged = acquire_experiment_package(
        source_root,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    assert not (staged.root / ".git").exists()
    assert not (staged.root / "results").exists()


def test_reopening_tampered_staged_package_fails_digest_check(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_minimal_package(source_root)
    staged = acquire_experiment_package(
        source_root,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    (staged.root / "README.md").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(experiment_package_error, match="SHA-256"):
        open_staged_experiment_package(
            runtime_root,
            EXPERIMENT_ID,
            staged.package_sha256,
            contract_schema_path=CONTRACT_SCHEMA,
        )


def test_staged_package_root_rejects_unsafe_identity(tmp_path: Path) -> None:
    with pytest.raises(experiment_package_error, match="safe path component"):
        staged_package_root(tmp_path, "../escape", "0" * 64)
