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


def _write_export(
    export_dir: Path,
    *,
    selected_count: int = 4,
    resolved_count: int = 4,
) -> None:
    export_dir.mkdir(parents=True)
    (export_dir / "runs.csv").write_text(
        "execution_selected_run_count,execution_resolved_run_count\n"
        f"{selected_count},{resolved_count}\n",
        encoding="utf-8",
    )
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

    for _, script_name, _, _, _ in analyze_execution._R_STAGES:
        (script_root / script_name).write_text("# test\n", encoding="utf-8")

    monkeypatch.setattr(analyze_execution, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(analyze_execution, "_EXPORT_ROOT", repo_root / "exports")
    monkeypatch.setattr(analyze_execution, "_R_SCRIPT_ROOT", script_root)
    monkeypatch.setattr(
        analyze_execution,
        "_preflight",
        lambda: ("/venv/bin/acmm", "/usr/bin/Rscript"),
    )

    def build_workbook(export_path: Path, output_path: Path) -> None:
        del export_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"test workbook")

    monkeypatch.setattr(analyze_execution, "build_analysis_workbook", build_workbook)
    monkeypatch.setattr(analyze_execution, "build_availability_workbook", build_workbook)
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

    workbook_calls: list[tuple[Path, Path]] = []

    def build_workbook(export_path: Path, output_path: Path) -> None:
        workbook_calls.append((export_path, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"test workbook")

    monkeypatch.setattr(analyze_execution, "_run_command", run_command)
    monkeypatch.setattr(analyze_execution, "_run_and_save", run_and_save)
    monkeypatch.setattr(analyze_execution, "build_analysis_workbook", build_workbook)
    monkeypatch.setattr(analyze_execution, "build_availability_workbook", build_workbook)

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
        "analyze_dimension_uncertainty.R",
        "analyze_availability.R",
    ]
    assert analysis_commands[1][0][-5:-2] == ["2000", "12345", "0.95"]
    assert analysis_commands[3][0][-5:-2] == ["2000", "12345", "0.95"]
    assert [path.name for _, path in analysis_commands] == [
        "point_estimates.txt",
        "hierarchical_intervals.txt",
        "dimension_scores.txt",
        "dimension_intervals.txt",
        "availability_analysis.txt",
    ]
    assert all(
        path.parent == export_dir / "analysis_output" / "logs" / "compliance"
        for _, path in analysis_commands[:4]
    )
    assert (
        analysis_commands[4][1].parent
        == export_dir / "analysis_output" / "logs" / "availability"
    )
    assert [Path(command[-2]).name for command, _ in analysis_commands[:4]] == [
        "requirement_estimates.csv",
        "requirement_intervals.csv",
        "dimension_scores.csv",
        "dimension_intervals.csv",
    ]
    assert all(
        Path(command[-2]).parent
        == export_dir / "analysis_output" / "data" / "compliance"
        for command, _ in analysis_commands[:4]
    )
    assert Path(analysis_commands[4][0][-2]).name == "analysis_output"
    assert all(
        command[-1] == "--population-policy=strict"
        for command, _ in analysis_commands
    )
    assert workbook_calls == [
        (
            export_dir,
            export_dir / "analysis_output" / "analysis_report.xlsx",
        ),
        (
            export_dir,
            export_dir / "analysis_output" / "availability_analysis.xlsx",
        ),
    ]


def test_incomplete_export_automatically_falls_back_to_available_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, execution_dir, export_dir = _prepare_repo(tmp_path, monkeypatch)

    def run_command(command: Sequence[str]) -> int:
        del command
        _write_export(export_dir, selected_count=4, resolved_count=3)
        return 0

    analysis_commands: list[list[str]] = []

    def run_and_save(command: Sequence[str], output_path: Path) -> int:
        del output_path
        analysis_commands.append(list(command))
        return 0

    monkeypatch.setattr(analyze_execution, "_run_command", run_command)
    monkeypatch.setattr(analyze_execution, "_run_and_save", run_and_save)

    assert analyze_execution.run_analysis_workflow(execution_dir) == 0
    captured = capsys.readouterr()

    assert len(analysis_commands) == 5
    assert all(
        command[-1] == "--population-policy=available-valid"
        for command in analysis_commands
    )
    assert "strict population is unavailable" in captured.err
    assert "falling back automatically" in captured.err



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


def test_workbook_failure_is_reported_after_r_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, execution_dir, export_dir = _prepare_repo(tmp_path, monkeypatch)

    def run_command(command: Sequence[str]) -> int:
        _write_export(export_dir)
        return 0

    def fail_workbook(export_path: Path, output_path: Path) -> None:
        del export_path
        del output_path
        raise analyze_execution.analysis_workbook_error("workbook failed")

    monkeypatch.setattr(analyze_execution, "_run_command", run_command)
    monkeypatch.setattr(analyze_execution, "_run_and_save", lambda command, path: 0)
    monkeypatch.setattr(analyze_execution, "build_analysis_workbook", fail_workbook)

    assert analyze_execution.run_analysis_workflow(execution_dir) == 1
    failed_path = (
        export_dir
        / "analysis_output"
        / "logs"
        / "compliance"
        / "analysis_report.failed.txt"
    )
    assert failed_path.read_text(encoding="utf-8") == "workbook failed\n"


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



def test_clean_legacy_flat_analysis_outputs_preserves_readable_artifacts(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "analysis_output"
    output_dir.mkdir()
    (output_dir / "legacy.csv").write_text("x\n1\n", encoding="utf-8")
    (output_dir / "legacy.txt").write_text("old\n", encoding="utf-8")
    (output_dir / "analysis_report.xlsx").write_bytes(b"xlsx")
    figures = output_dir / "figures"
    figures.mkdir()
    (figures / "figure.png").write_bytes(b"png")

    analyze_execution._clean_legacy_flat_analysis_outputs(output_dir)

    assert not (output_dir / "legacy.csv").exists()
    assert not (output_dir / "legacy.txt").exists()
    assert (output_dir / "analysis_report.xlsx").exists()
    assert (figures / "figure.png").exists()

def test_saved_command_publishes_success_output_without_echoing_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "analysis_output" / "result.txt"
    command = [sys.executable, "-c", "print('analysis complete')"]

    assert analyze_execution._run_and_save(command, output_path) == 0
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert output_path.read_text(encoding="utf-8") == "analysis complete\n"
    assert not analyze_execution._failed_output_path(output_path).exists()


def test_saved_command_preserves_failed_output_and_shows_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "analysis_output" / "result.txt"
    command = [
        sys.executable,
        "-c",
        (
            "import sys; print('analysis failed'); "
            "print('diagnostic', file=sys.stderr); raise SystemExit(6)"
        ),
    ]

    assert analyze_execution._run_and_save(command, output_path) == 6
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "diagnostic\n"
    assert not output_path.exists()
    assert (
        analyze_execution._failed_output_path(output_path).read_text(encoding="utf-8")
        == "analysis failed\n\n--- stderr ---\ndiagnostic\n"
    )


def test_experiment_3_runs_registered_supplementary_interaction_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "controlled_recovery_boundary_interaction_v1"
    repo_root = tmp_path / "repo"
    execution_dir = repo_root / "results" / experiment_id / f"execution_{EXECUTION_ID}"
    export_dir = repo_root / "exports" / experiment_id / f"execution_{EXECUTION_ID}"
    script_root = repo_root / "analysis" / "r" / "scripts"
    execution_dir.mkdir(parents=True)
    script_root.mkdir(parents=True)
    interaction_design = (
        repo_root
        / "experiments"
        / "examples"
        / "controlled_recovery_boundary_interaction"
        / "interaction_analysis.yaml"
    )
    interaction_design.parent.mkdir(parents=True)
    interaction_design.write_text("analysis_kind: interaction\n", encoding="utf-8")

    manifest = {
        "schema_version": "0.1",
        "execution": {"id": EXECUTION_ID, "experiment_id": experiment_id},
        "slots": [],
    }
    (execution_dir / "experiment_execution.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(analyze_execution, "_REPO_ROOT", repo_root)
    monkeypatch.setattr(analyze_execution, "_EXPORT_ROOT", repo_root / "exports")
    monkeypatch.setattr(analyze_execution, "_R_SCRIPT_ROOT", script_root)
    monkeypatch.setattr(
        analyze_execution,
        "_preflight",
        lambda supplementary_script_name=None: ("/venv/bin/acmm", "/usr/bin/Rscript"),
    )

    def write_export() -> None:
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / "runs.csv").write_text(
            "execution_selected_run_count,execution_resolved_run_count\n4,4\n",
            encoding="utf-8",
        )
        (export_dir / "judgments.csv").write_text("outcome\n", encoding="utf-8")
        export_manifest = {
            "schema_version": "0.1",
            "source": {
                "execution_id": EXECUTION_ID,
                "experiment_id": experiment_id,
            },
        }
        (export_dir / "export_manifest.yaml").write_text(
            yaml.safe_dump(export_manifest, sort_keys=False),
            encoding="utf-8",
        )

    def run_command(command: Sequence[str]) -> int:
        del command
        write_export()
        return 0

    analysis_commands: list[list[str]] = []

    def run_and_save(command: Sequence[str], output_path: Path) -> int:
        analysis_commands.append(list(command))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("ok\n", encoding="utf-8")
        return 0

    base_workbooks: list[Path] = []
    availability_workbooks: list[Path] = []
    interaction_workbooks: list[Path] = []

    def build_base(export_path: Path, output_path: Path) -> None:
        del export_path
        base_workbooks.append(output_path)
        output_path.write_bytes(b"base")

    def build_availability(export_path: Path, output_path: Path) -> None:
        del export_path
        availability_workbooks.append(output_path)
        output_path.write_bytes(b"availability")

    interaction_designs: list[Path] = []

    def build_interaction(
        export_path: Path,
        output_path: Path,
        design_path: Path,
    ) -> None:
        del export_path
        interaction_workbooks.append(output_path)
        interaction_designs.append(design_path)
        output_path.write_bytes(b"interaction")

    monkeypatch.setattr(analyze_execution, "_run_command", run_command)
    monkeypatch.setattr(analyze_execution, "_run_and_save", run_and_save)
    monkeypatch.setattr(analyze_execution, "build_analysis_workbook", build_base)
    monkeypatch.setattr(
        analyze_execution,
        "build_availability_workbook",
        build_availability,
    )
    monkeypatch.setattr(
        analyze_execution,
        "build_interaction_workbook",
        build_interaction,
    )

    assert analyze_execution.run_analysis_workflow(execution_dir) == 0
    assert [Path(command[1]).name for command in analysis_commands] == [
        "analyze_export.R",
        "analyze_uncertainty.R",
        "analyze_dimensions.R",
        "analyze_dimension_uncertainty.R",
        "analyze_availability.R",
        "analyze_interactions.R",
    ]
    assert analysis_commands[-1][-1] == "--population-policy=strict"
    assert (
        Path(analysis_commands[-1][1]).name == "analyze_interactions.R"
    )
    assert Path(analysis_commands[-1][-2]) == interaction_design
    assert base_workbooks == [export_dir / "analysis_output" / "analysis_report.xlsx"]
    assert availability_workbooks == [
        export_dir / "analysis_output" / "availability_analysis.xlsx"
    ]
    assert interaction_workbooks == [
        export_dir / "analysis_output" / "interaction_analysis.xlsx"
    ]
    assert interaction_designs == [interaction_design]
