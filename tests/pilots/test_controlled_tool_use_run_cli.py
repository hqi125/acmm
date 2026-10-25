from __future__ import annotations

import json
from pathlib import Path
import subprocess

import yaml

from acmm.cli import _build_parser
from acmm.live_preflight import (
    live_readiness_preflight_result,
    subject_live_readiness,
)
from acmm_infra.deployment import write_experiment_apparatus_state
from acmm_infra.profiles.experiments import experiment_apparatus_sha256


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    ROOT
    / "experiments"
    / "examples"
    / "controlled_tool_use_multiscenario"
    / "experiment.yaml"
)


def _ready_live_preflight(*, subject_bindings, **kwargs):
    del kwargs
    return live_readiness_preflight_result(
        subjects=tuple(
            subject_live_readiness(
                subject_id=subject_id,
                infrastructure_subject=binding.infrastructure_subject,
                experiment_id=None,
                checks=(),
                error=None,
            )
            for subject_id, binding in subject_bindings.items()
        )
    )


def _prepare_hermes_experiment_context(
    tmp_path: Path,
    experiment_id: str,
) -> Path:
    runtime_root = tmp_path / "runtime"
    deployment_root = runtime_root / "hermes"
    write_experiment_apparatus_state(
        deployment_root,
        experiment_id=experiment_id,
        source_sha256=experiment_apparatus_sha256(
            ROOT,
            "hermes",
            experiment_id,
        ),
        application_scope="experiment",
        resources=[],
    )
    return runtime_root


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
    monkeypatch.setattr("acmm.cli.inspect_live_readiness", _ready_live_preflight)
    runtime_root = _prepare_hermes_experiment_context(
        tmp_path,
        "controlled_tool_use_multiscenario_v1",
    )
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
            "--runtime-root",
            str(runtime_root),
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
    assert "Experiment execution:" in output
    assert "  run:        15/59" in output
    assert "  scenario:   addition_failure" in output
    assert f"summary:      {summary_path}" in output
    assert "selected:     1 of 60" in output
    assert "resolved:     1 of 1 selected" in output
    assert "terminations: agent_completed=1" in output
    assert "duration:" in output
    assert "judgment stop_after_failure: compliant" not in output
    assert "tool calls: get_magic_number, add_numbers" not in output




def test_run_command_previews_subject_range_without_execution(
    tmp_path: Path,
    capsys,
) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            str(CONTRACT_PATH),
            "--run-index",
            "15-44",
            "--subject",
            "open_webui_nemotron30b",
            "--preview",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--results-root",
            str(tmp_path),
        ]
    )

    assert args.handler(args) == 0
    assert list(tmp_path.iterdir()) == []

    captured = capsys.readouterr()
    output = captured.out
    assert "Run selection preview" in output
    assert "Deployment context preflight:" in output
    assert "open_webui_nemotron30b" in captured.err
    assert "no active experiment deployment context" in captured.err
    assert "selected:      20" in output
    assert "subjects:      open_webui_nemotron30b" in output
    assert "run indices:   20-39" in output
    assert "open_webui_nemotron30b / single_addition_success:" in output
    assert "      runs:        20-24" in output
    assert "open_webui_nemotron30b / double_addition_success:" in output
    assert "      runs:        25-29" in output
    assert "open_webui_nemotron30b / retrieval_failure:" in output
    assert "      runs:        30-34" in output
    assert "open_webui_nemotron30b / addition_failure:" in output
    assert "      runs:        35-39" in output
    assert "No execution started (--preview)." in output
