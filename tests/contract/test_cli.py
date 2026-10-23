from pathlib import Path

from acmm.cli import _build_parser


ROOT = Path(__file__).resolve().parents[2]


def test_validate_command_accepts_valid_contract() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "validate",
            str(
                ROOT
                / "tests"
                / "fixtures"
                / "contracts"
                / "valid"
                / "minimal.yaml"
            ),
        ]
    )

    assert args.handler(args) == 0


def test_validate_command_rejects_invalid_contract() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "validate",
            str(
                ROOT
                / "tests"
                / "fixtures"
                / "contracts"
                / "invalid"
                / "unresolved_requirement.yaml"
            ),
        ]
    )

    assert args.handler(args) == 1


def test_plan_command_prints_deterministic_runs(capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "plan",
            str(
                ROOT
                / "experiments"
                / "examples"
                / "magic_number"
                / "experiment.yaml"
            ),
        ]
    )

    assert args.handler(args) == 0

    output = capsys.readouterr().out

    assert "Experiment: magic_number_tool_use_v1" in output
    assert "Runs: 5" in output
    assert "INDEX" in output
    assert "SUBJECT" in output
    assert "SCENARIO" in output
    assert "REPETITION" in output

    run_lines = [
        line
        for line in output.splitlines()
        if "hermes_nemotron30b" in line
    ]

    assert len(run_lines) == 5
    assert run_lines[0].split() == [
        "0",
        "hermes_nemotron30b",
        "magic_plus_13",
        "1",
    ]
    assert run_lines[-1].split() == [
        "4",
        "hermes_nemotron30b",
        "magic_plus_13",
        "5",
    ]


def test_plan_command_rejects_invalid_contract(capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "plan",
            str(
                ROOT
                / "tests"
                / "fixtures"
                / "contracts"
                / "invalid"
                / "unresolved_requirement.yaml"
            ),
        ]
    )

    assert args.handler(args) == 1

    output = capsys.readouterr().out

    assert "unresolved_requirement_reference" in output
    assert "Experiment:" not in output


def test_plan_command_allows_contract_warnings(capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "plan",
            str(
                ROOT
                / "tests"
                / "fixtures"
                / "contracts"
                / "valid"
                / "unused_check_warning.yaml"
            ),
        ]
    )

    assert args.handler(args) == 0

    output = capsys.readouterr().out

    assert "unused_check" in output
    assert "Experiment: minimal_valid" in output
    assert "Runs: 1" in output


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
            str(
                ROOT
                / "experiments"
                / "examples"
                / "controlled_tool_use_multiscenario"
                / "experiment.yaml"
            ),
            "--run-index",
            "0",
            "--run-index",
            "5",
            "--run-index",
            "20",
        ]
    )

    assert args.run_index == [0, 5, 20]
