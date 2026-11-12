from __future__ import annotations

from pathlib import Path

from acmm.generated_paths import (
    default_exports_root,
    default_generated_root,
    default_results_root,
)


def test_generated_paths_default_under_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ACMM_GENERATED_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    root = default_generated_root()

    assert root == (tmp_path / "acmm_generated").resolve()
    assert default_results_root() == root / "results"
    assert default_exports_root() == root / "exports"


def test_generated_paths_honor_environment_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "generated-override"
    monkeypatch.setenv("ACMM_GENERATED_ROOT", str(configured))

    root = default_generated_root()

    assert root == configured.resolve()
    assert default_results_root() == root / "results"
    assert default_exports_root() == root / "exports"

def test_main_cli_defaults_use_generated_namespaces() -> None:
    from acmm.cli import _build_parser

    parser = _build_parser()
    run_args = parser.parse_args(["run", "example_experiment", "--preview"])
    export_args = parser.parse_args(["export", "/tmp/execution_example"])

    assert run_args.results_root == str(default_results_root())
    assert export_args.output_root == str(default_exports_root())

