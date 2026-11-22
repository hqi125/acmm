from pathlib import Path

from acmm.cli import _build_parser
from acmm.contract.loader import load_contract
from acmm.runtime.planner import build_experiment_plan


ROOT = Path(__file__).resolve().parents[2]



def test_export_run_command_parses_analysis_export_arguments() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "export",
            "run",
            "results/example/execution_123",
            "--output-root",
            "exports-test",
        ]
    )

    assert args.command == "export"
    assert args.export_scope == "run"
    assert args.execution == "results/example/execution_123"
    assert args.output_root == "exports-test"


def test_export_campaign_command_parses_independent_campaign_arguments() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "export",
            "campaign",
            "generated/example/campaign_123",
            "generated/example/campaign_123/merged/merge_456",
            "--output-root",
            "exports-test",
        ]
    )

    assert args.command == "export"
    assert args.export_scope == "campaign"
    assert args.campaign == "generated/example/campaign_123"
    assert args.merge == "generated/example/campaign_123/merged/merge_456"
    assert args.output_root == "exports-test"



def test_export_provider_pressure_command_parses_independent_arguments() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "export",
            "provider-pressure",
            "generated/provider_pressure",
            "--output-root",
            "exports-test",
        ]
    )

    assert args.command == "export"
    assert args.export_scope == "provider-pressure"
    assert args.pressure_root == "generated/provider_pressure"
    assert args.output_root == "exports-test"



def test_analyze_command_parses_plan_and_export_arguments() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "analyze",
            "exports/example/campaign_123/merge_456",
            "--plan",
            "analysis-plan.yaml",
            "--output-root",
            "analysis-results",
        ]
    )

    assert args.command == "analyze"
    assert args.export == "exports/example/campaign_123/merge_456"
    assert args.plan == "analysis-plan.yaml"
    assert args.output_root == "analysis-results"


def test_run_command_accepts_repeated_run_indices() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "package_smoke_v1",
            "--run-index",
            "0",
            "--run-index",
            "5",
            "--run-index",
            "20",
        ]
    )

    assert args.run_index == ["0", "5", "20"]
    assert args.subject is None
    assert args.preview is False
    assert args.capture_runtime_logs is False
    assert args.capture_environment_snapshots is False
    assert args.max_attempts_per_run == 2


def test_run_command_parses_ranges_subjects_and_preview() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "package_smoke_v1",
            "--run-index",
            "20-39",
            "--subject",
            "open_webui_nemotron30b",
            "--preview",
            "--capture-runtime-logs",
            "--capture-environment-snapshots",
        ]
    )

    assert args.run_index == ["20-39"]
    assert args.subject == ["open_webui_nemotron30b"]
    assert args.preview is True
    assert args.capture_runtime_logs is True
    assert args.capture_environment_snapshots is True


def test_run_command_parses_subject_relative_run_index() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "package_smoke_v1",
            "--subject",
            "open_webui_gpt_oss120b",
            "--subject-run-index",
            "0-1",
            "--preview",
        ]
    )

    assert args.run_index is None
    assert args.subject_run_index == ["0-1"]
    assert args.subject == ["open_webui_gpt_oss120b"]
    assert args.preview is True


def test_run_command_parses_semantic_scenario_and_repetition_filters() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "package_smoke_v1",
            "--subject",
            "agent_zero_gpt_oss120b",
            "--scenario",
            "smoke",
            "--repetition",
            "1-2",
            "--preview",
        ]
    )

    assert args.subject == ["agent_zero_gpt_oss120b"]
    assert args.scenario == ["smoke"]
    assert args.repetition == ["1-2"]
    assert args.preview is True
