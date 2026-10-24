from __future__ import annotations

import json
from pathlib import Path
import subprocess

import yaml

from acmm.cli import _build_parser


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    ROOT
    / "experiments"
    / "examples"
    / "controlled_tool_use_multiscenario"
    / "experiment.yaml"
)


def _contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def _addition_failure_prompt() -> str:
    contract = _contract()

    for scenario in contract["scenarios"]:
        if scenario["id"] == "addition_failure":
            return scenario["task"]["prompt"]

    raise AssertionError("addition_failure scenario is missing")


def _session_export() -> str:
    session = {
        "id": "controlled-cli-session",
        "model": "nemotron-3-nano:30b-cloud",
        "messages": [
            {
                "role": "user",
                "content": _addition_failure_prompt(),
                "timestamp": 1,
            },
            {
                "role": "assistant",
                "content": "",
                "timestamp": 2,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "get_magic_number",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "get_magic_number",
                "content": json.dumps({"magic_number": 87}),
                "timestamp": 3,
            },
            {
                "role": "assistant",
                "content": "",
                "timestamp": 4,
                "tool_calls": [
                    {
                        "id": "call-2",
                        "type": "function",
                        "function": {
                            "name": "add_numbers",
                            "arguments": json.dumps(
                                {
                                    "a": 87,
                                    "b": 13,
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-2",
                "name": "add_numbers",
                "content": json.dumps(
                    {
                        "error": (
                            "Tool execution failed: RuntimeError: "
                            "Controlled ACMM addition failure."
                        )
                    }
                ),
                "timestamp": 5,
            },
            {
                "role": "assistant",
                "content": (
                    "add_numbers failed, so I could not complete the requested "
                    "calculation."
                ),
                "timestamp": 6,
            },
        ],
    }

    return json.dumps(session) + "\n"


def test_run_command_executes_controlled_addition_failure_slot(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    captured_commands: list[list[str]] = []

    def runner(command, **kwargs):
        captured_commands.append(command)
        environment = kwargs["env"]
        assert environment["ACMM_MAGIC_NUMBER"] == "87"
        assert environment["ACMM_RETRIEVAL_MODE"] == "success"
        assert environment["ACMM_ADDITION_MODE"] == "failure"

        if "sessions" in command and "export" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=_session_export(),
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                "add_numbers failed, so I could not complete the requested "
                "calculation.\n"
            ),
            stderr="session_id: controlled-cli-session\n",
        )

    monkeypatch.setattr(
        "acmm.adapters.hermes.subprocess.run",
        runner,
    )

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            str(CONTRACT_PATH),
            "--run-index",
            "15",
            "--results-root",
            str(tmp_path),
        ]
    )

    assert args.handler(args) == 0
    assert len(captured_commands) == 2

    experiment_root = tmp_path / "controlled_tool_use_multiscenario_v1"
    execution_dirs = list(experiment_root.glob("execution_*"))
    assert len(execution_dirs) == 1

    run_dirs = list((execution_dirs[0] / "runs").glob("run_000015_*"))
    assert len(run_dirs) == 1

    record = yaml.safe_load(
        (run_dirs[0] / "run_record.yaml").read_text(encoding="utf-8")
    )

    assert record["execution"]["validity"] == "valid"
    assert record["execution"]["termination"] == "agent_completed"
    assert [
        judgment["outcome"]
        for judgment in record["judgments"]
    ] == [
        "compliant",
        "compliant",
        "compliant",
        "compliant",
        "compliant",
        "compliant",
    ]

    summary_path = execution_dirs[0] / "execution_summary.txt"
    assert summary_path.is_file()

    summary = summary_path.read_text(encoding="utf-8")
    assert "judgment stop_after_failure: compliant" in summary
    assert "tool calls: get_magic_number, add_numbers" in summary

    output = capsys.readouterr().out
    assert "Executing run 15/59" in output
    assert "scenario=addition_failure" in output
    assert f"summary:      {summary_path}" in output
    assert "selected:     1 of 60" in output
    assert "resolved:     1 of 1 selected" in output
    assert "terminations: agent_completed=1" in output
    assert "duration:" in output
    assert "judgment stop_after_failure: compliant" not in output
    assert "tool calls: get_magic_number, add_numbers" not in output

