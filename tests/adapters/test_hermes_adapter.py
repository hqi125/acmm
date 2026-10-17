from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from acmm.adapters.hermes import hermes_adapter, hermes_adapter_config


SUBJECT = {
    "id": "hermes_nemotron30b",
    "agent": {
        "name": "Hermes Agent",
        "version": "0.21.3",
    },
    "model": {
        "name": "nemotron-3-nano:30b-cloud",
        "provider": "ollama_cloud",
    },
    "configuration": {
        "context_window": 65536,
    },
}

SCENARIO = {
    "id": "magic_plus_13",
    "task": {
        "prompt": (
            "Use the laboratory magic number and the add_numbers tool to add "
            "the magic number to 13."
        )
    },
}

TERMINATION = {
    "max_steps": 32,
    "timeout_seconds": 120,
}

ENVIRONMENT = {
    "magic_number": 87,
}


def _stream(
    *,
    result_exit_code: int = 0,
    result_text: str = "The result is 100.",
    result_error: str | None = None,
) -> str:
    events = [
        {
            "type": "system",
            "subtype": "init",
            "timestamp": 1,
            "model": "nemotron-3-nano:30b-cloud",
            "session_id": "session-123",
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "name": "get_magic_number",
            "input": {},
        },
        {
            "type": "tool_result",
            "timestamp": 3,
            "name": "get_magic_number",
            "output": json.dumps({"magic_number": 87}),
            "duration_ms": 1,
            "is_error": False,
        },
        {
            "type": "tool_use",
            "timestamp": 4,
            "name": "add_numbers",
            "input": {
                "a": 87,
                "b": 13,
            },
        },
        {
            "type": "tool_result",
            "timestamp": 5,
            "name": "add_numbers",
            "output": json.dumps({"result": 100}),
            "duration_ms": 1,
            "is_error": False,
        },
        {
            "type": "text",
            "timestamp": 6,
            "text": result_text,
        },
        {
            "type": "result",
            "timestamp": 7,
            "session_id": "session-123",
            "exit_code": result_exit_code,
            "text": result_text,
        },
    ]

    if result_error is not None:
        events[-1]["error"] = result_error

    return "\n".join(json.dumps(event) for event in events) + "\n"


def test_hermes_adapter_builds_structured_evidence() -> None:
    captured: dict = {}

    def runner(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=_stream(),
            stderr="",
        )

    adapter = hermes_adapter(
        hermes_adapter_config(
            provider="configured-provider",
            reasoning="medium",
        ),
        runner=runner,
    )

    result = adapter.execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    assert result.termination == "agent_completed"
    assert result.subject_provenance is not None
    assert result.subject_provenance["configuration"]["hermes_session_id"] == (
        "session-123"
    )
    assert [item.id for item in result.evidence] == [
        "conversation",
        "tool_calls",
        "tool_results",
        "final_response",
    ]

    command = captured["command"]
    assert command[:2] == ["hermes", "chat"]
    assert "--oneshot" in command
    assert command[command.index("--format") + 1] == "stream-json"
    assert command[command.index("--model") + 1] == "nemotron-3-nano:30b-cloud"
    assert command[command.index("--toolsets") + 1] == "acmm"
    assert command[command.index("--max-turns") + 1] == "32"
    assert command[command.index("--run-budget") + 1] == "120"
    assert command[command.index("--provider") + 1] == "configured-provider"
    assert command[command.index("--reasoning") + 1] == "medium"

    assert captured["kwargs"]["env"]["ACMM_MAGIC_NUMBER"] == "87"
    assert captured["kwargs"]["timeout"] == 135.0

    conversation = json.loads(result.evidence[0].content)
    assert conversation["messages"][0]["role"] == "user"
    assert conversation["messages"][0]["content"] == SCENARIO["task"]["prompt"]
    assert conversation["messages"][1]["content"] == "The result is 100."


def test_docker_mode_injects_fixture_value_into_container() -> None:
    captured: dict = {}

    def runner(command, **kwargs):
        captured["command"] = command

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=_stream(),
            stderr="",
        )

    adapter = hermes_adapter(
        hermes_adapter_config(
            docker_container="hermes-agent",
        ),
        runner=runner,
    )

    result = adapter.execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    assert captured["command"][:7] == [
        "docker",
        "exec",
        "-i",
        "--env",
        "ACMM_MAGIC_NUMBER=87",
        "hermes-agent",
        "hermes",
    ]


def test_run_budget_exhaustion_is_valid_agent_timeout() -> None:
    def runner(command, **kwargs):
        del kwargs

        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout=_stream(
                result_exit_code=1,
                result_text="",
                result_error="run budget timeout exceeded",
            ),
            stderr="",
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    assert result.termination == "timeout"
    assert len(result.evidence) == 4


def test_iteration_budget_is_valid_max_steps_termination() -> None:
    def runner(command, **kwargs):
        del kwargs

        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout=_stream(
                result_exit_code=1,
                result_text="",
                result_error="maximum iteration budget reached",
            ),
            stderr="",
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    assert result.termination == "max_steps"


def test_invalid_stream_becomes_infrastructure_failure() -> None:
    def runner(command, **kwargs):
        del kwargs

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="not-json\n",
            stderr="",
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.termination == "harness_error"
    assert result.failure is not None
    assert result.failure.kind == "invalid_hermes_stream"


def test_process_timeout_becomes_infrastructure_failure() -> None:
    def runner(command, **kwargs):
        del kwargs
        raise subprocess.TimeoutExpired(command, 135)

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.termination == "harness_error"
    assert result.failure is not None
    assert result.failure.kind == "adapter_process_timeout"
