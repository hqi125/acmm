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
    / "magic_number"
    / "experiment.yaml"
)


def _session_export() -> str:
    session = {
        "id": "cli-pilot-session",
        "model": "nemotron-3-nano:30b-cloud",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Use the laboratory magic number and the add_numbers tool to a"
                    "dd the magic number to 13."
                ),
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
                "content": json.dumps({"result": 100}),
                "timestamp": 5,
            },
            {
                "role": "assistant",
                "content": (
                    "The laboratory magic number is 87, and 87 + 13 = 100."
                ),
                "timestamp": 6,
            },
        ],
    }

    return json.dumps(session) + "\n"


def test_run_command_executes_one_magic_number_slot(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    captured_commands: list[list[str]] = []

    def runner(command, **kwargs):
        captured_commands.append(command)
        assert kwargs["env"]["ACMM_MAGIC_NUMBER"] == "87"

        if "sessions" in command and "export" in command:
            assert command[:5] == [
                "docker",
                "exec",
                "-i",
                "acmm-hermes",
                "/opt/hermes/bin/hermes",
            ]
            assert command[-7:] == [
                "sessions",
                "export",
                "-",
                "--session-id",
                "cli-pilot-session",
                "--format",
                "jsonl",
            ]

            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=_session_export(),
                stderr="",
            )

        assert command[:7] == [
            "docker",
            "exec",
            "-i",
            "--env",
            "ACMM_MAGIC_NUMBER=87",
            "acmm-hermes",
            "/opt/hermes/bin/hermes",
        ]
        assert command[7:9] == ["chat", "--oneshot"]
        assert "-Q" in command
        assert "--format" not in command
        assert "--model" not in command
        assert command[command.index("--toolsets") + 1] == "acmm"

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                "The laboratory magic number is 87, and "
                "87 + 13 = 100.\n"
            ),
            stderr="session_id: cli-pilot-session\n",
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
            "0",
            "--results-root",
            str(tmp_path),
        ]
    )

    assert args.handler(args) == 0
    assert len(captured_commands) == 2

    experiment_root = tmp_path / "magic_number_tool_use_v1"
    execution_dirs = list(experiment_root.glob("execution_*"))
    assert len(execution_dirs) == 1

    execution_dir = execution_dirs[0]
    manifest = yaml.safe_load(
        (execution_dir / "experiment_execution.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["execution"]["contract_run_count"] == 5
    assert manifest["execution"]["selected_run_indices"] == [0]
    assert manifest["execution"]["planned_run_count"] == 1
    assert manifest["execution"]["resolved_run_count"] == 1
    assert manifest["execution"]["pacing"] == {
        "inter_run_delay_seconds": 2.0,
    }

    run_dirs = list((execution_dir / "runs").glob("run_000000_*"))
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
    ]

    summary_path = execution_dir / "execution_summary.txt"
    assert summary_path.is_file()

    summary = summary_path.read_text(encoding="utf-8")
    assert "judgment retrieve: compliant" in summary
    assert "judgment calculate: compliant" in summary
    assert "tool calls: get_magic_number, add_numbers" in summary
    assert "final response:" in summary

    output = capsys.readouterr().out
    assert "Executing run 0/4" in output
    assert "toolset overlay: acmm" in output
    assert "Inter-run pacing:" not in output
    assert f"summary:      {summary_path}" in output
    assert "selected:     1 of 5" in output
    assert "resolved:     1 of 1 selected" in output
    assert "terminations: agent_completed=1" in output
    assert "duration:" in output
    assert "judgment retrieve: compliant" not in output
    assert "tool calls: get_magic_number, add_numbers" not in output


def test_run_command_rejects_out_of_range_index(
    tmp_path: Path,
    capsys,
) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            str(CONTRACT_PATH),
            "--run-index",
            "5",
            "--results-root",
            str(tmp_path),
        ]
    )

    assert args.handler(args) == 2
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []

    captured = capsys.readouterr()
    assert "outside the Contract plan range 0..4" in captured.err
