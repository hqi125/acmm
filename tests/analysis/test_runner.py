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
        for relative in runner._expected_outputs_for_stage(stage_kind, kwargs["plan"]):
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
        for relative in runner._expected_outputs_for_stage(stage_kind, kwargs["plan"]):
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
        for relative in runner._expected_outputs_for_stage(stage_kind, kwargs["plan"]):
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
        {
            "kind": "trajectory_run_metrics",
            "metric": "error_rate",
            "formats": ["png", "pdf"],
        },
        {
            "kind": "trajectory_tool_usage",
            "top_n": 7,
            "formats": ["png", "pdf"],
        },
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

    assert command[-5:] == ("dimension_scores", "png,pdf", "scenario", "minimum", "false")


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

    assert command[-4:] == (
        "run_duration",
        "png,pdf",
        str(tmp_path / "export"),
        "available-valid",
    )


def test_v03_plot_stage_outputs_follow_requested_formats(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        """schema_version: '0.3'
population:
  policy: strict
defaults:
  plot_formats:
    - png
analyses:
  - kind: requirement_estimates
plots:
  - kind: dimension_profile
    estimand: opportunity
    aggregation_method: arithmetic
""",
        encoding="utf-8",
        newline="",
    )
    plan = runner.load_analysis_plan(plan_path)

    assert runner._expected_outputs_for_stage(
        "plot_dimension_profile", plan
    ) == ("figures/compliance/dimension_profile.png",)
    command = runner._stage_command(
        "plot_dimension_profile",
        tmp_path / "export",
        tmp_path / "analysis",
        plan,
        "Rscript",
        None,
    )
    assert command[-4:] == (
        "dimension_profile",
        "png",
        "opportunity",
        "arithmetic",
    )


def test_v04_trajectory_plot_stage_commands_and_outputs(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        """schema_version: '0.4'
population:
  policy: strict
analyses:
  - kind: trajectory
plots:
  - kind: trajectory_error_rate
  - kind: trajectory_tool_composition
    top_n: 10
""",
        encoding="utf-8",
        newline="",
    )
    plan = runner.load_analysis_plan(plan_path)

    assert runner._expected_outputs_for_stage(
        "plot_trajectory_error_rate", plan
    ) == ("figures/trajectory/trajectory_error_rate.png",)
    assert runner._expected_outputs_for_stage(
        "plot_trajectory_tool_composition", plan
    ) == ("figures/trajectory/trajectory_tool_composition.png",)

    error_command = runner._stage_command(
        "plot_trajectory_error_rate",
        tmp_path / "export",
        tmp_path / "analysis",
        plan,
        "Rscript",
        None,
    )
    assert error_command[-2:] == ("trajectory_error_rate", "png")

    composition_command = runner._stage_command(
        "plot_trajectory_tool_composition",
        tmp_path / "export",
        tmp_path / "analysis",
        plan,
        "Rscript",
        None,
    )
    assert composition_command[-3:] == (
        "trajectory_tool_composition",
        "png",
        "10",
    )


def test_provider_pressure_plan_binds_pressure_export_without_population(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_dir = tmp_path / "pressure_export"
    export_dir.mkdir(parents=True)
    manifest = {
        "schema_version": "0.2",
        "source": {
            "kind": "provider_pressure",
            "source_digest_sha256": "a" * 64,
            "lineage_count": 1,
            "snapshot_count": 3,
            "trajectory_schema_versions": ["0.5"],
            "lineages": [
                {
                    "lineage_id": "lineage_fixture",
                    "provider_id": "github",
                    "scope_id": "scope_fixture",
                    "model_id": "github_pressure_v1",
                    "model_version": "0.13",
                    "model_definition_sha256": "b" * 64,
                    "snapshot_count": 3,
                    "latest_sequence": 2,
                    "latest_snapshot_sha256": "c" * 64,
                }
            ],
        },
        "tables": {},
    }
    (export_dir / "export_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
        newline="",
    )
    plan_path = Path(__file__).resolve().parents[2] / "analysis" / "plans" / "provider_pressure_v0_1.yaml"
    monkeypatch.setattr(runner, "_preflight_runtime", lambda plan: _runtime())

    stages: list[str] = []

    def execute_stage(**kwargs: object) -> None:
        stage_kind = str(kwargs["stage_kind"])
        analysis_dir = Path(kwargs["analysis_dir"])
        stages.append(stage_kind)
        for relative in runner._expected_outputs_for_stage(stage_kind, kwargs["plan"]):
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

    assert result.source_kind == "provider_pressure"
    assert result.population_policy is None
    assert stages == [
        "provider_pressure_summary",
        "plot_provider_pressure_pacing",
        "plot_provider_pressure_feature_scores",
    ]
    manifest_out = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest_out["source"]["kind"] == "provider_pressure"
    assert manifest_out["source"]["source_digest_sha256"] == "a" * 64
    assert manifest_out["plan"]["source_kind"] == "provider_pressure"
    assert "population_policy" not in manifest_out["plan"]
    assert any("source kind 'provider_pressure'" in message for message in progress)


def test_provider_pressure_v02_plan_resolves_admission_wait_plot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_dir = tmp_path / "pressure_export"
    export_dir.mkdir(parents=True)
    manifest = {
        "schema_version": "0.5",
        "source": {
            "kind": "provider_pressure",
            "source_digest_sha256": "a" * 64,
            "lineage_count": 1,
            "snapshot_count": 3,
            "trajectory_schema_versions": ["segmented-0.2"],
            "lineages": [],
        },
        "tables": {},
    }
    (export_dir / "export_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
        newline="",
    )
    plan_path = Path(__file__).resolve().parents[2] / "analysis" / "plans" / "provider_pressure_v0_2.yaml"
    monkeypatch.setattr(runner, "_preflight_runtime", lambda plan: _runtime())

    stages: list[str] = []

    def execute_stage(**kwargs: object) -> None:
        stage_kind = str(kwargs["stage_kind"])
        analysis_dir = Path(kwargs["analysis_dir"])
        stages.append(stage_kind)
        for relative in runner._expected_outputs_for_stage(stage_kind, kwargs["plan"]):
            path = analysis_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{stage_kind}\n", encoding="utf-8", newline="")

    monkeypatch.setattr(runner, "_execute_stage", execute_stage)
    result = run_analysis_plan(
        export_dir,
        plan_path,
        output_root=tmp_path / "analysis",
    )

    assert result.source_kind == "provider_pressure"
    assert stages == [
        "provider_pressure_summary",
        "plot_provider_pressure_pacing",
        "plot_provider_pressure_admission_waits",
    ]


def test_provider_pressure_plan_rejects_campaign_export(tmp_path: Path) -> None:
    export_dir = tmp_path / "export"
    _write_export(export_dir)
    plan_path = Path(__file__).resolve().parents[2] / "analysis" / "plans" / "provider_pressure_v0_1.yaml"

    with pytest.raises(analysis_run_error, match="require a Provider Pressure export"):
        run_analysis_plan(export_dir, plan_path, output_root=tmp_path / "analysis")


def test_provider_pressure_v03_resolves_final_descriptive_plot_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_dir = tmp_path / "pressure_export"
    export_dir.mkdir(parents=True)
    manifest = {
        "schema_version": "0.6",
        "source": {
            "kind": "provider_pressure",
            "source_digest_sha256": "a" * 64,
            "lineage_count": 1,
            "snapshot_count": 3,
            "trajectory_schema_versions": ["segmented-0.2"],
            "lineages": [],
        },
        "tables": {},
    }
    (export_dir / "export_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
        newline="",
    )
    plan_path = Path(__file__).resolve().parents[2] / "analysis" / "plans" / "provider_pressure_v0_3.yaml"
    monkeypatch.setattr(runner, "_preflight_runtime", lambda plan: _runtime())

    stages: list[str] = []

    def execute_stage(**kwargs: object) -> None:
        stage_kind = str(kwargs["stage_kind"])
        analysis_dir = Path(kwargs["analysis_dir"])
        stages.append(stage_kind)
        for relative in runner._expected_outputs_for_stage(stage_kind, kwargs["plan"]):
            path = analysis_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{stage_kind}\n", encoding="utf-8", newline="")

    monkeypatch.setattr(runner, "_execute_stage", execute_stage)
    result = run_analysis_plan(
        export_dir,
        plan_path,
        output_root=tmp_path / "analysis",
    )

    assert result.source_kind == "provider_pressure"
    assert stages == [
        "provider_pressure_summary",
        "plot_provider_pressure_utilization_envelope",
        "plot_provider_pressure_utilization_by_subject",
        "plot_provider_pressure_admission_waits",
        "plot_provider_pressure_wait_vs_actions",
    ]


def test_provider_pressure_v03_requires_export_v06(tmp_path: Path) -> None:
    export_dir = tmp_path / "pressure_export"
    export_dir.mkdir(parents=True)
    manifest = {
        "schema_version": "0.5",
        "source": {
            "kind": "provider_pressure",
            "source_digest_sha256": "a" * 64,
            "lineage_count": 1,
            "snapshot_count": 3,
            "trajectory_schema_versions": ["segmented-0.2"],
            "lineages": [],
        },
        "tables": {},
    }
    (export_dir / "export_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
        newline="",
    )
    plan_path = Path(__file__).resolve().parents[2] / "analysis" / "plans" / "provider_pressure_v0_3.yaml"

    with pytest.raises(analysis_run_error, match="export schema 0.6"):
        run_analysis_plan(export_dir, plan_path, output_root=tmp_path / "analysis")
