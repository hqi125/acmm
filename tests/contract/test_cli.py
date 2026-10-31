from pathlib import Path

from acmm.cli import (
    _build_parser,
    _resolve_selected_runs,
)
from acmm.contract.loader import load_contract
from acmm.runtime.planner import build_experiment_plan


ROOT = Path(__file__).resolve().parents[2]



def test_export_command_parses_analysis_export_arguments() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "export",
            "results/example/execution_123",
            "--output-root",
            "exports-test",
        ]
    )

    assert args.command == "export"
    assert args.execution == "results/example/execution_123"
    assert args.output_root == "exports-test"


def test_run_command_accepts_repeated_run_indices() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "controlled_tool_use_multiscenario_v1",
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


def test_run_command_parses_ranges_subjects_and_preview() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "controlled_tool_use_multiscenario_v1",
            "--run-index",
            "20-39",
            "--subject",
            "open_webui_nemotron30b",
            "--preview",
            "--capture-runtime-logs",
        ]
    )

    assert args.run_index == ["20-39"]
    assert args.subject == ["open_webui_nemotron30b"]
    assert args.preview is True
    assert args.capture_runtime_logs is True


def test_run_selection_intersects_ranges_and_subjects() -> None:
    contract_path = (
        ROOT
        / "experiments"
        / "examples"
        / "controlled_tool_use_multiscenario"
        / "experiment.yaml"
    )
    contract = load_contract(contract_path)
    plan = build_experiment_plan(contract)

    selected = _resolve_selected_runs(
        plan,
        contract,
        ["15-25", "20-30"],
        ["open_webui_nemotron30b"],
    )

    assert [run.run_index for run in selected] == list(range(20, 31))
    assert {run.subject_id for run in selected} == {"open_webui_nemotron30b"}
