from __future__ import annotations

import hashlib
from pathlib import Path
import sys

import pytest
import yaml

from acmm.analysis import runner
from acmm.analysis.runner import analysis_run_error, run_analysis_plan


def _write_export(export_dir: Path) -> None:
    export_dir.mkdir(parents=True)
    manifest = {
        "schema_version": "0.2",
        "source": {
            "kind": "campaign",
            "campaign_id": "campaign_fixture",
            "merge_id": "merge_fixture",
            "experiment_id": "experiment_fixture",
            "package_sha256": "a" * 64,
            "contract_sha256": "b" * 64,
        },
        "tables": {},
    }
    (export_dir / "export_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
        newline="",
    )


def _write_plan(path: Path) -> None:
    path.write_text(
        """schema_version: '0.1'\npopulation:\n  policy: strict\nanalyses:\n  - kind: requirement_estimates\n  - kind: requirement_uncertainty\n    bootstrap:\n      replicates: 50\n      seed: 9\n      confidence_level: 0.9\nreports: []\n""",
        encoding="utf-8",
        newline="",
    )


def _runtime() -> runner._runtime_context:
    return runner._runtime_context(
        rscript="Rscript",
        rscript_version="R scripting front-end version fixture",
        python_version="3.fixture",
        acmm_version="0.1.fixture",
        xlsxwriter_version=None,
        r_packages={"digest": "0.6", "jsonlite": "2.0", "yaml": "2.3"},
    )


def test_analysis_plan_run_publishes_manifest_and_hashed_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_dir = tmp_path / "export"
    plan_path = tmp_path / "plan.yaml"
    _write_export(export_dir)
    _write_plan(plan_path)
    monkeypatch.setattr(runner, "_preflight_runtime", lambda plan: _runtime())

    stages: list[str] = []

    def execute_stage(**kwargs: object) -> None:
        stage_kind = str(kwargs["stage_kind"])
        analysis_dir = Path(kwargs["analysis_dir"])
        stages.append(stage_kind)
        for relative in runner._STAGE_DEFINITIONS[stage_kind].expected_outputs:
            path = analysis_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{stage_kind}\n", encoding="utf-8", newline="")

    monkeypatch.setattr(runner, "_execute_stage", execute_stage)
    progress: list[str] = []

    result = run_analysis_plan(
        export_dir,
        plan_path,
        output_root=tmp_path / "analysis",
        progress=progress.append,
    )

    assert stages == ["requirement_estimates", "requirement_uncertainty"]
    assert result.output_dir.is_dir()
    assert not result.output_dir.name.startswith(".")
    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "0.1"
    assert manifest["source"]["campaign_id"] == "campaign_fixture"
    assert manifest["source"]["merge_id"] == "merge_fixture"
    assert manifest["plan"]["population_policy"] == "strict"
    assert manifest["plan"]["resolved_stages"] == stages
    artifact_by_name = {
        item["artifact"]: item for item in manifest["artifacts"]
    }
    for artifact, binding in artifact_by_name.items():
        data = (result.output_dir / artifact).read_bytes()
        assert hashlib.sha256(data).hexdigest() == binding["sha256"]
        assert len(data) == binding["bytes"]
    assert any("[1/2]" in message for message in progress)
    assert any("published atomically" in message for message in progress)


def test_analysis_run_refuses_to_overwrite_same_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_dir = tmp_path / "export"
    plan_path = tmp_path / "plan.yaml"
    _write_export(export_dir)
    _write_plan(plan_path)
    monkeypatch.setattr(runner, "_preflight_runtime", lambda plan: _runtime())

    def execute_stage(**kwargs: object) -> None:
        stage_kind = str(kwargs["stage_kind"])
        analysis_dir = Path(kwargs["analysis_dir"])
        for relative in runner._STAGE_DEFINITIONS[stage_kind].expected_outputs:
            path = analysis_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8", newline="")

    monkeypatch.setattr(runner, "_execute_stage", execute_stage)
    output_root = tmp_path / "analysis"
    run_analysis_plan(export_dir, plan_path, output_root=output_root)

    with pytest.raises(analysis_run_error, match="Refusing to overwrite"):
        run_analysis_plan(export_dir, plan_path, output_root=output_root)


def test_r_stage_logs_all_output_but_forwards_only_progress_lines(
    tmp_path: Path,
) -> None:
    messages: list[str] = []
    log_path = tmp_path / "stage.txt"
    command = (
        sys.executable,
        "-c",
        "print('ordinary output'); print('ACMM_PROGRESS: bootstrap halfway'); "
        "print('more ordinary output')",
    )

    runner._run_r_stage(command, log_path, messages.append)

    text = log_path.read_text(encoding="utf-8")
    assert "ordinary output" in text
    assert "bootstrap halfway" in text
    assert "more ordinary output" in text
    assert messages == ["bootstrap halfway"]


def test_failed_stage_is_preserved_in_failed_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_dir = tmp_path / "export"
    plan_path = tmp_path / "plan.yaml"
    _write_export(export_dir)
    _write_plan(plan_path)
    monkeypatch.setattr(runner, "_preflight_runtime", lambda plan: _runtime())

    def fail_stage(**kwargs: object) -> None:
        analysis_dir = Path(kwargs["analysis_dir"])
        (analysis_dir / "diagnostic.txt").write_text(
            "failure\n",
            encoding="utf-8",
            newline="",
        )
        raise analysis_run_error("fixture stage failed", exit_code=7)

    monkeypatch.setattr(runner, "_execute_stage", fail_stage)
    output_root = tmp_path / "analysis"

    with pytest.raises(analysis_run_error) as error:
        run_analysis_plan(export_dir, plan_path, output_root=output_root)

    assert error.value.exit_code == 7
    failed = list(output_root.glob("analysis_*.failed"))
    assert len(failed) == 1
    assert (failed[0] / "diagnostic.txt").is_file()


def test_v02_plan_records_plot_requests_and_resolved_trajectory_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_dir = tmp_path / "export"
    export_dir.mkdir(parents=True)
    manifest = {
        "schema_version": "0.3",
        "source": {
            "kind": "campaign",
            "campaign_id": "campaign_fixture",
            "merge_id": "merge_fixture",
            "experiment_id": "experiment_fixture",
            "package_sha256": "a" * 64,
            "contract_sha256": "b" * 64,
        },
        "tables": {},
    }
    (export_dir / "export_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
        newline="",
    )
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        """schema_version: '0.2'\npopulation:\n  policy: strict\nanalyses:\n  - kind: trajectory\nplots:\n  - kind: trajectory_run_metrics\n    metric: error_rate\n  - kind: trajectory_tool_usage\n    top_n: 7\n""",
        encoding="utf-8",
        newline="",
    )
    monkeypatch.setattr(runner, "_preflight_runtime", lambda plan: _runtime())

    stages: list[str] = []

    def execute_stage(**kwargs: object) -> None:
        stage_kind = str(kwargs["stage_kind"])
        analysis_dir = Path(kwargs["analysis_dir"])
        stages.append(stage_kind)
        for relative in runner._STAGE_DEFINITIONS[stage_kind].expected_outputs:
            path = analysis_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8", newline="")

    monkeypatch.setattr(runner, "_execute_stage", execute_stage)
    result = run_analysis_plan(
        export_dir,
        plan_path,
        output_root=tmp_path / "analysis",
    )

    assert stages == [
        "trajectory",
        "plot_trajectory_run_metrics",
        "plot_trajectory_tool_usage",
    ]
    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["plan"]["requested_analyses"] == ["trajectory"]
    assert manifest["plan"]["requested_plots"] == [
        {"kind": "trajectory_run_metrics", "metric": "error_rate"},
        {"kind": "trajectory_tool_usage", "top_n": 7},
    ]


def test_plot_stage_command_uses_plan_parameters(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        """schema_version: '0.2'\npopulation:\n  policy: strict\nanalyses:\n  - kind: requirement_estimates\nplots:\n  - kind: dimension_scores\n    estimand: scenario\n    aggregation_method: minimum\n    include_intervals: false\n""",
        encoding="utf-8",
        newline="",
    )
    plan = runner.load_analysis_plan(plan_path)
    command = runner._stage_command(
        "plot_dimension_scores",
        tmp_path / "export",
        tmp_path / "analysis",
        plan,
        "Rscript",
        None,
    )

    assert command[-4:] == ("dimension_scores", "scenario", "minimum", "false")


def test_trajectory_plan_rejects_legacy_campaign_export_before_runtime_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_dir = tmp_path / "export"
    _write_export(export_dir)
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        """schema_version: '0.2'
population:
  policy: strict
analyses:
  - kind: trajectory
""",
        encoding="utf-8",
        newline="",
    )

    def unexpected_runtime(plan: object) -> object:
        raise AssertionError("runtime preflight must not run for incompatible export")

    monkeypatch.setattr(runner, "_preflight_runtime", unexpected_runtime)

    with pytest.raises(analysis_run_error, match="requires Campaign analysis export schema 0.3"):
        run_analysis_plan(export_dir, plan_path, output_root=tmp_path / "analysis")


def test_run_duration_plot_stage_receives_export_and_population_policy(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        """schema_version: '0.2'
population:
  policy: available-valid
analyses:
  - kind: requirement_estimates
plots:
  - kind: run_duration
""",
        encoding="utf-8",
        newline="",
    )
    plan = runner.load_analysis_plan(plan_path)
    command = runner._stage_command(
        "plot_run_duration",
        tmp_path / "export",
        tmp_path / "analysis",
        plan,
        "Rscript",
        None,
    )

    assert command[-3:] == (
        "run_duration",
        str(tmp_path / "export"),
        "available-valid",
    )
