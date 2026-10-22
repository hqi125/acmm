from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Sequence

import pytest
import yaml


_REPO_ROOT = Path(__file__).resolve().parents[2]
_ANALYZE_EXECUTION_PATH = _REPO_ROOT / "tools" / "analyze_execution.py"
_ANALYZE_EXECUTION_SPEC = importlib.util.spec_from_file_location(
    "acmm_repo_analyze_execution",
    _ANALYZE_EXECUTION_PATH,
)

assert _ANALYZE_EXECUTION_SPEC is not None
assert _ANALYZE_EXECUTION_SPEC.loader is not None

analyze_execution = importlib.util.module_from_spec(_ANALYZE_EXECUTION_SPEC)
sys.modules[_ANALYZE_EXECUTION_SPEC.name] = analyze_execution
_ANALYZE_EXECUTION_SPEC.loader.exec_module(analyze_execution)


EXECUTION_ID = "11111111-1111-4111-8111-111111111111"
EXPERIMENT_ID = "example_experiment_v1"


def _write_execution(execution_dir: Path) -> None:
    execution_dir.mkdir(parents=True)
    manifest = {
        "schema_version": "0.1",
        "execution": {
            "id": EXECUTION_ID,
            "experiment_id": EXPERIMENT_ID,
        },
        "slots": [],
    }
    (execution_dir / "experiment_execution.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )


def _write_export(export_dir: Path) -> None:
    export_dir.mkdir(parents=True)
    (export_dir / "runs.csv").write_text("run_id\n", encoding="utf-8")
    (export_dir / "judgments.csv").write_text("outcome\n", encoding="utf-8")
    manifest = {
        "schema_version": "0.1",
        "source": {
            "execution_id": EXECUTION_ID,
            "experiment_id": EXPERIMENT_ID,
        },
    }
    (export_dir / "export_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )


def _prepare_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path]:
    repo_root = tmp_path / "repo"
    execution_dir = repo_root / "results" / EXPERIMENT_ID / f"execution_{EXECUTION_ID}"
    export_dir = repo_root / "exports" / EXPERIMENT_ID / f"execution_{EXECUTION_ID}"
    script_root = repo_root / "analysis" / "r" / "scripts"

    _write_execution(execution_dir)
    script_root.mkdir(parents=True)

    for _, script_name, _, _ in analyze_execution._R_STAGES:
        (script_root / script_name).write_text("# test\n", encoding="utf-8")

    monkeypatch.setattr(analyze_execution, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(analyze_execution, "_EXPORT_ROOT", repo_root / "exports")
    monkeypatch.setattr(analyze_execution, "_R_SCRIPT_ROOT", script_root)
    monkeypatch.setattr(
        analyze_execution,
        "_preflight",
        lambda: ("/venv/bin/acmm", "/usr/bin/Rscript"),
    )
    return repo_root, execution_dir, export_dir


def test_workflow_exports_then_runs_all_analysis_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root, execution_dir, export_dir = _prepare_repo(tmp_path, monkeypatch)
    export_commands: list[list[str]] = []
    analysis_commands: list[tuple[list[str], Path]] = []

    def run_command(command: Sequence[str]) -> int:
        export_commands.append(list(command))
        _write_export(export_dir)
        return 0

    def run_and_save(command: Sequence[str], output_path: Path) -> int:
        analysis_commands.append((list(command), output_path))
        return 0

    monkeypatch.setattr(analyze_execution, "_run_command", run_command)
    monkeypatch.setattr(analyze_execution, "_run_and_save", run_and_save)

    assert analyze_execution.run_analysis_workflow(execution_dir) == 0
    assert export_commands == [[
        "/venv/bin/acmm",
        "export",
        str(execution_dir.resolve()),
        "--output-root",
        str(repo_root / "exports"),
    ]]
    assert [Path(command[1]).name for command, _ in analysis_commands] == [
        "analyze_export.R",
        "analyze_uncertainty.R",
        "analyze_dimensions.R",
    ]
    assert analysis_commands[1][0][-3:] == ["2000", "12345", "0.95"]
    assert [path.name for _, path in analysis_commands] == [
        "point_estimates.txt",
        "hierarchical_intervals.txt",
        "dimension_scores.txt",
    ]


def test_existing_matching_export_is_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, execution_dir, export_dir = _prepare_repo(tmp_path, monkeypatch)
    _write_export(export_dir)

    monkeypatch.setattr(
        analyze_execution,
        "_run_command",
        lambda command: (_ for _ in ()).throw(AssertionError("export should not run")),
    )
    monkeypatch.setattr(analyze_execution, "_run_and_save", lambda command, path: 0)

    assert analyze_execution.run_analysis_workflow(execution_dir) == 0


def test_export_failure_stops_before_r(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, execution_dir, _ = _prepare_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(analyze_execution, "_run_command", lambda command: 7)
    monkeypatch.setattr(
        analyze_execution,
        "_run_and_save",
        lambda command, path: (_ for _ in ()).throw(
            AssertionError("R analysis should not run")
        ),
    )

    assert analyze_execution.run_analysis_workflow(execution_dir) == 7


def test_r_failure_stops_later_analysis_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, execution_dir, export_dir = _prepare_repo(tmp_path, monkeypatch)

    def run_command(command: Sequence[str]) -> int:
        _write_export(export_dir)
        return 0

    analysis_calls: list[str] = []

    def run_and_save(command: Sequence[str], output_path: Path) -> int:
        analysis_calls.append(Path(command[1]).name)
        return 9

    monkeypatch.setattr(analyze_execution, "_run_command", run_command)
    monkeypatch.setattr(analyze_execution, "_run_and_save", run_and_save)

    assert analyze_execution.run_analysis_workflow(execution_dir) == 9
    assert analysis_calls == ["analyze_export.R"]


def test_malformed_execution_manifest_fails_cleanly(tmp_path: Path) -> None:
    execution_dir = tmp_path / "execution_invalid"
    execution_dir.mkdir()
    (execution_dir / "experiment_execution.yaml").write_text(
        "schema_version: '0.1'\n",
        encoding="utf-8",
    )

    with pytest.raises(
        analyze_execution.analysis_workflow_error,
        match="must define an 'execution' mapping",
    ):
        analyze_execution._load_execution_identity(execution_dir)


def test_mismatched_existing_export_is_rejected(tmp_path: Path) -> None:
    export_dir = tmp_path / f"execution_{EXECUTION_ID}"
    _write_export(export_dir)
    manifest_path = export_dir / "export_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["execution_id"] = "22222222-2222-4222-8222-222222222222"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    with pytest.raises(
        analyze_execution.analysis_workflow_error,
        match="does not match the requested execution",
    ):
        analyze_execution._validate_reusable_export(
            export_dir,
            EXECUTION_ID,
            EXPERIMENT_ID,
        )


def test_saved_command_publishes_success_output(tmp_path: Path) -> None:
    output_path = tmp_path / "analysis_output" / "result.txt"
    command = [sys.executable, "-c", "print('analysis complete')"]

    assert analyze_execution._run_and_save(command, output_path) == 0
    assert output_path.read_text(encoding="utf-8") == "analysis complete\n"
    assert not analyze_execution._failed_output_path(output_path).exists()


def test_saved_command_preserves_failed_output(tmp_path: Path) -> None:
    output_path = tmp_path / "analysis_output" / "result.txt"
    command = [
        sys.executable,
        "-c",
        "import sys; print('analysis failed'); raise SystemExit(6)",
    ]

    assert analyze_execution._run_and_save(command, output_path) == 6
    assert not output_path.exists()
    assert (
        analyze_execution._failed_output_path(output_path).read_text(encoding="utf-8")
        == "analysis failed\n"
    )
