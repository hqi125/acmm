from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from acmm.cli import _build_parser
from acmm.experiment_package import install_experiment_package
from acmm.subjects.catalog import lookup_subject_execution_binding
from acmm_infra.deployment import write_experiment_apparatus_state
from experiment_package.support import (
    CONTRACT_SCHEMA,
    MAGIC_RUNTIME_SOURCE,
    install_test_package,
    write_package_context,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "experiments" / "examples" / "magic_number" / "experiment.yaml"
EXPERIMENT_ID = "magic_number_tool_use_v1"
SUBJECT_ID = "hermes_nemotron30b"


def test_run_preview_resolves_unique_installed_experiment(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, _ = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=MAGIC_RUNTIME_SOURCE,
    )

    parser = _build_parser()
    args = parser.parse_args(["run", EXPERIMENT_ID, "--subject", SUBJECT_ID, "--preview"])

    assert args.handler(args) == 0
    captured = capsys.readouterr()
    assert "installed Experiment Package" in captured.out
    assert EXPERIMENT_ID in captured.out
    assert package.package_sha256 in captured.out
    assert "Run selection preview:" in captured.out
    assert "no active experiment deployment context" in captured.err


def test_run_preview_accepts_digest_qualified_selector(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=MAGIC_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, SUBJECT_ID)
    selector = f"{EXPERIMENT_ID}@{package.package_sha256[:12]}"

    parser = _build_parser()
    args = parser.parse_args(["run", selector, "--subject", SUBJECT_ID, "--preview"])

    assert args.handler(args) == 0
    captured = capsys.readouterr()
    assert selector in captured.out
    assert "context:           ready" in captured.out
    assert captured.err == ""


def test_run_rejects_ambiguous_installed_experiment_selector(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    first, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=MAGIC_RUNTIME_SOURCE,
    )
    source = tmp_path / "package-source"
    (source / "README.md").write_text("second revision\n", encoding="utf-8")
    second = install_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    assert first.package_sha256 != second.package_sha256

    parser = _build_parser()
    args = parser.parse_args(["run", EXPERIMENT_ID, "--preview"])

    assert args.handler(args) == 2
    captured = capsys.readouterr()
    assert "ambiguous" in captured.err
    assert "Run selection preview" not in captured.out


def test_run_uses_installed_snapshot_after_source_is_removed(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=MAGIC_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, SUBJECT_ID)
    shutil.rmtree(tmp_path / "package-source")

    parser = _build_parser()
    args = parser.parse_args(["run", EXPERIMENT_ID, "--subject", SUBJECT_ID, "--preview"])

    assert args.handler(args) == 0
    captured = capsys.readouterr()
    assert str(package.root) in captured.out
    assert captured.err == ""


def test_run_does_not_infer_experiment_from_deployment_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    binding = lookup_subject_execution_binding(SUBJECT_ID)
    assert binding is not None
    write_experiment_apparatus_state(
        runtime_root / binding.infrastructure_subject,
        experiment_id=EXPERIMENT_ID,
        source_sha256="0" * 64,
        application_scope="experiment",
        resources=[],
    )

    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--subject", SUBJECT_ID, "--preview"])


def test_explicit_run_selector_is_not_replaced_by_deployment_experiment(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=MAGIC_RUNTIME_SOURCE,
    )
    binding = lookup_subject_execution_binding(SUBJECT_ID)
    assert binding is not None
    write_experiment_apparatus_state(
        runtime_root / binding.infrastructure_subject,
        experiment_id="different_experiment_v1",
        source_sha256=None,
        application_scope="experiment",
        resources=[],
    )

    parser = _build_parser()
    args = parser.parse_args(["run", EXPERIMENT_ID, "--subject", SUBJECT_ID, "--preview"])

    assert args.handler(args) == 0
    captured = capsys.readouterr()
    assert package.experiment_id in captured.out
    assert "active experiment is 'different_experiment_v1'" in captured.err


@pytest.mark.parametrize(
    "retired_args",
    (
        ("--package", "some-package"),
        ("--repo-root", "/tmp/repo"),
        ("--runtime-root", "/tmp/runtime"),
        ("--schema", "/tmp/schema.json"),
    ),
)
def test_run_parser_rejects_retired_experiment_source_options(
    retired_args: tuple[str, str],
) -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", EXPERIMENT_ID, *retired_args])


@pytest.mark.parametrize("command", ("validate", "plan"))
def test_parser_rejects_retired_raw_contract_commands(command: str) -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([command, str(CONTRACT)])
    assert exc_info.value.code == 2
