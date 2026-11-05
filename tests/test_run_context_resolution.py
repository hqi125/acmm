from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from acmm.cli import _build_parser
from acmm.experiment_package import install_experiment_package
from acmm.live_preflight import (
    live_readiness_preflight_result,
    subject_live_readiness,
)
from acmm.subjects.catalog import lookup_subject_execution_binding
from experiment_package.support import (
    CONTRACT_SCHEMA,
    PACKAGE_SMOKE_CONTRACT,
    SMOKE_RUNTIME_SOURCE,
    install_test_package,
    write_package_context,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = PACKAGE_SMOKE_CONTRACT
EXPERIMENT_ID = "package_smoke_v1"
SUBJECT_ID = "hermes_gpt_oss120b"


def test_run_preview_resolves_unique_installed_experiment(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, _ = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=SMOKE_RUNTIME_SOURCE,
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
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, SUBJECT_ID)
    selector = f"{EXPERIMENT_ID}@{package.package_sha256[:12]}"

    parser = _build_parser()
    args = parser.parse_args(["run", selector, "--subject", SUBJECT_ID, "--preview"])

    assert args.handler(args) == 0
    captured = capsys.readouterr()
    assert selector in captured.out
    assert "context:         ready" in captured.out
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
        runtime_source=SMOKE_RUNTIME_SOURCE,
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
        runtime_source=SMOKE_RUNTIME_SOURCE,
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
    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, SUBJECT_ID)

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
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, SUBJECT_ID)
    marker = runtime_root / "hermes" / ".acmm-experiment-package.json"
    import json
    state = json.loads(marker.read_text(encoding="utf-8"))
    state["experiment_id"] = "different_experiment_v1"
    marker.write_text(json.dumps(state), encoding="utf-8")

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


def test_run_preview_reports_strict_failure_for_diverged_context(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )

    parser = _build_parser()
    args = parser.parse_args(
        ["run", EXPERIMENT_ID, "--subject", SUBJECT_ID, "--preview"]
    )

    assert args.handler(args) == 0
    captured = capsys.readouterr()
    assert "context:         not-ready" in captured.out
    assert "reconciliation is not permitted" in captured.err
    assert "Execution would fail in strict mode" in captured.err
    assert "--apply-if-needed" not in captured.out


def test_run_preview_reports_permitted_reconciliation_without_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            EXPERIMENT_ID,
            "--subject",
            SUBJECT_ID,
            "--preview",
            "--apply-if-needed",
        ]
    )

    assert args.handler(args) == 0
    captured = capsys.readouterr()
    assert "context:         not-ready" in captured.out
    assert "reconciliation is permitted by --apply-if-needed" in captured.out
    assert "followed by a second strict deployment preflight" in captured.out
    assert "reconciliation is not permitted" not in captured.err

    binding = lookup_subject_execution_binding(SUBJECT_ID)
    assert binding is not None
    assert not (runtime_root / binding.infrastructure_subject).exists()
    assert package.root.exists()


def test_run_apply_if_needed_reconciles_only_selected_subjects_and_rechecks(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )
    selected: list[tuple[str, ...]] = []

    def fake_apply_selected_package_context(**kwargs) -> None:
        selected.append(kwargs["selected_subject_ids"])

    monkeypatch.setattr(
        "acmm.cli._apply_selected_package_context",
        fake_apply_selected_package_context,
    )

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            EXPERIMENT_ID,
            "--subject",
            SUBJECT_ID,
            "--apply-if-needed",
        ]
    )

    assert args.handler(args) == 2
    captured = capsys.readouterr()
    assert selected == [(SUBJECT_ID,)]
    assert "Experiment context is not converged" in captured.out
    assert "Deployment context preflight after reconciliation:" in captured.out
    assert "Deployment context preflight failed" in captured.err
    assert "no execution session was created" in captured.err


def test_run_apply_if_needed_requires_successful_second_preflight(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )

    def fake_apply_selected_package_context(**kwargs) -> None:
        assert kwargs["selected_subject_ids"] == (SUBJECT_ID,)
        write_package_context(package, runtime_root, SUBJECT_ID)

    binding = lookup_subject_execution_binding(SUBJECT_ID)
    assert binding is not None

    def fake_live_readiness(**kwargs):
        return live_readiness_preflight_result(
            subjects=(
                subject_live_readiness(
                    subject_id=SUBJECT_ID,
                    infrastructure_subject=binding.infrastructure_subject,
                    experiment_id=EXPERIMENT_ID,
                    checks=(),
                    error="test stop after deployment reconciliation",
                ),
            )
        )

    monkeypatch.setattr(
        "acmm.cli._apply_selected_package_context",
        fake_apply_selected_package_context,
    )
    monkeypatch.setattr("acmm.cli.inspect_live_readiness", fake_live_readiness)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            EXPERIMENT_ID,
            "--subject",
            SUBJECT_ID,
            "--apply-if-needed",
        ]
    )

    assert args.handler(args) == 2
    captured = capsys.readouterr()
    assert "Deployment context preflight after reconciliation:" in captured.out
    assert "context:         ready" in captured.out
    assert "Deployment context preflight failed" not in captured.err
    assert "Live readiness preflight failed" in captured.err
