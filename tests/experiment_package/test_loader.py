from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from acmm.experiment_package import (
    experiment_package_error,
    load_experiment_package,
)
from acmm.experiment_package.schema import load_package_manifest_schema


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = ROOT / "contracts" / "v0_1" / "contract.schema.json"
VALID_CONTRACT = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "magic_number.yaml"
EXPERIMENT_ID = "magic_number_tool_use_v1"


def _write_package(root: Path, *, full: bool = True) -> None:
    root.mkdir(parents=True)
    shutil.copy2(VALID_CONTRACT, root / "experiment.yaml")

    manifest = (
        'schema_version: "0.1"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "contract: experiment.yaml\n"
    )

    if full:
        manifest += (
            "runtime:\n"
            '  api_version: "0.1"\n'
            "  root: runtime\n"
            "  entrypoint: build_runtime\n"
            "apparatus:\n"
            "  hermes: apparatus/hermes\n"
            "  open-webui: apparatus/open-webui\n"
            "analysis:\n"
            "  supplementary:\n"
            "    - kind: interaction\n"
            "      configuration: analysis/interaction.yaml\n"
        )

    (root / "experiment-package.yaml").write_text(manifest, encoding="utf-8")

    if not full:
        return

    runtime_root = root / "runtime"
    runtime_root.mkdir()
    (runtime_root / "__init__.py").write_text(
        "def build_runtime(context):\n    return context\n",
        encoding="utf-8",
    )
    (runtime_root / "fixtures.py").write_text("VALUE = 1\n", encoding="utf-8")

    for subject in ("hermes", "open-webui"):
        apparatus_root = root / "apparatus" / subject
        apparatus_root.mkdir(parents=True)
        (apparatus_root / "apparatus.yaml").write_text(
            'schema_version: "0.1"\n'
            f"experiment_id: {EXPERIMENT_ID}\n",
            encoding="utf-8",
        )

    analysis_root = root / "analysis"
    analysis_root.mkdir()
    (analysis_root / "interaction.yaml").write_text(
        'schema_version: "0.1"\n',
        encoding="utf-8",
    )


def test_load_experiment_package_enumerates_declared_components(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root)

    package = load_experiment_package(
        package_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    assert package.root == package_root.resolve()
    assert package.schema_version == "0.1"
    assert package.experiment_id == EXPERIMENT_ID
    assert package.contract_path == package_root.resolve() / "experiment.yaml"
    assert len(package.package_sha256) == 64
    assert len(package.contract_sha256) == 64
    assert package.runtime is not None
    assert package.runtime.api_version == "0.1"
    assert package.runtime.root == package_root.resolve() / "runtime"
    assert package.runtime.entrypoint == "build_runtime"
    assert len(package.runtime.sha256) == 64
    assert tuple(package.apparatus) == ("hermes", "open-webui")
    assert package.apparatus["hermes"].root == package_root.resolve() / "apparatus/hermes"
    assert len(package.apparatus["hermes"].sha256) == 64
    assert len(package.analysis) == 1
    assert package.analysis[0].kind == "interaction"
    assert package.analysis[0].configuration == (
        package_root.resolve() / "analysis/interaction.yaml"
    )


def test_minimal_package_requires_only_manifest_and_contract(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root, full=False)

    package = load_experiment_package(
        package_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    assert package.runtime is None
    assert not package.apparatus
    assert package.analysis == ()


def test_package_digest_is_deterministic_and_component_specific(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root)

    first = load_experiment_package(
        package_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    runtime_sha256 = first.runtime.sha256 if first.runtime is not None else None
    apparatus_sha256 = first.apparatus["hermes"].sha256

    (package_root / "README.md").write_text("Research notes.\n", encoding="utf-8")
    second = load_experiment_package(
        package_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    assert second.package_sha256 != first.package_sha256
    assert second.contract_sha256 == first.contract_sha256
    assert second.runtime is not None
    assert second.runtime.sha256 == runtime_sha256
    assert second.apparatus["hermes"].sha256 == apparatus_sha256


def test_package_digest_ignores_fixed_generated_artifacts(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root)
    first = load_experiment_package(
        package_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    (package_root / "runtime" / "__pycache__").mkdir()
    (package_root / "runtime" / "__pycache__" / "cache.pyc").write_bytes(b"cache")
    (package_root / ".git").mkdir()
    (package_root / ".git" / "index").write_bytes(b"git metadata")
    (package_root / "results").mkdir()
    (package_root / "results" / "run.yaml").write_text("result: 1\n")

    second = load_experiment_package(
        package_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    assert second.package_sha256 == first.package_sha256
    assert second.runtime is not None
    assert first.runtime is not None
    assert second.runtime.sha256 == first.runtime.sha256


def test_manifest_rejects_unknown_fields(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root, full=False)
    with (package_root / "experiment-package.yaml").open("a", encoding="utf-8") as file:
        file.write("unexpected: true\n")

    with pytest.raises(experiment_package_error, match="schema violation"):
        load_experiment_package(
            package_root,
            contract_schema_path=CONTRACT_SCHEMA,
        )


def test_manifest_rejects_contract_path_traversal(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root, full=False)
    outside = tmp_path / "outside.yaml"
    shutil.copy2(VALID_CONTRACT, outside)
    (package_root / "experiment-package.yaml").write_text(
        'schema_version: "0.1"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "contract: ../outside.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(experiment_package_error, match="safe package-relative path"):
        load_experiment_package(
            package_root,
            contract_schema_path=CONTRACT_SCHEMA,
        )


def test_manifest_and_contract_experiment_ids_must_match(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root, full=False)
    (package_root / "experiment-package.yaml").write_text(
        'schema_version: "0.1"\n'
        "experiment_id: different_experiment_v1\n"
        "contract: experiment.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(experiment_package_error, match="does not match Contract"):
        load_experiment_package(
            package_root,
            contract_schema_path=CONTRACT_SCHEMA,
        )


def test_apparatus_experiment_id_must_match_package(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root)
    apparatus = package_root / "apparatus" / "hermes" / "apparatus.yaml"
    apparatus.write_text(
        'schema_version: "0.1"\nexperiment_id: wrong_experiment_v1\n',
        encoding="utf-8",
    )

    with pytest.raises(experiment_package_error, match="apparatus experiment_id"):
        load_experiment_package(
            package_root,
            contract_schema_path=CONTRACT_SCHEMA,
        )


def test_runtime_root_must_contain_python_package_entrypoint(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root)
    (package_root / "runtime" / "__init__.py").unlink()

    with pytest.raises(experiment_package_error, match="must contain '__init__.py'"):
        load_experiment_package(
            package_root,
            contract_schema_path=CONTRACT_SCHEMA,
        )


def test_managed_package_symlink_is_rejected(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root, full=False)
    target = tmp_path / "external.txt"
    target.write_text("external\n", encoding="utf-8")
    (package_root / "external-link.txt").symlink_to(target)

    with pytest.raises(experiment_package_error, match="symbolic links"):
        load_experiment_package(
            package_root,
            contract_schema_path=CONTRACT_SCHEMA,
        )


def test_declared_resource_must_be_managed_package_content(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root, full=False)
    results_root = package_root / "results"
    results_root.mkdir()
    shutil.copy2(VALID_CONTRACT, results_root / "experiment.yaml")
    (package_root / "experiment-package.yaml").write_text(
        'schema_version: "0.1"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "contract: results/experiment.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(experiment_package_error, match="managed Experiment Package content"):
        load_experiment_package(
            package_root,
            contract_schema_path=CONTRACT_SCHEMA,
        )


def test_package_root_symlink_is_rejected(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root, full=False)
    link = tmp_path / "package-link"
    link.symlink_to(package_root, target_is_directory=True)

    with pytest.raises(experiment_package_error, match="root must not be a symbolic link"):
        load_experiment_package(
            link,
            contract_schema_path=CONTRACT_SCHEMA,
        )


def test_package_manifest_schema_v0_1_loads_from_json_file() -> None:
    schema = load_package_manifest_schema("0.1")

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == "0.1"


def test_manifest_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    _write_package(package_root, full=False)
    (package_root / "experiment-package.yaml").write_text(
        'schema_version: "0.2"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "contract: experiment.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(
        experiment_package_error,
        match="Unsupported Experiment Package schema_version '0.2'",
    ):
        load_experiment_package(
            package_root,
            contract_schema_path=CONTRACT_SCHEMA,
        )
