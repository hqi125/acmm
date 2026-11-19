from __future__ import annotations

import json
import subprocess

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
        "context_window": 64000,
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
    "ACMM_MAGIC_NUMBER": "87",
}


def _session_export(
    *,
    model: str = "nemotron-3-nano:30b-cloud",
    final_response: str = "The result is 100.",
) -> str:
    session = {
        "id": "session-123",
        "model": model,
        "source": "tool",
        "messages": [
            {
                "role": "user",
                "content": SCENARIO["task"]["prompt"],
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
                "content": final_response,
                "timestamp": 6,
            },
        ],
    }

    return json.dumps(session) + "\n"


def _successful_runner(
    calls: list[tuple[list[str], dict]],
    *,
    model: str = "nemotron-3-nano:30b-cloud",
):
    def runner(command, **kwargs):
        calls.append((list(command), kwargs))

        if "sessions" in command and "export" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=_session_export(model=model),
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="The result is 100.\n",
            stderr="\nsession_id: session-123\n",
        )

    return runner



def test_hermes_adapter_allows_timeout_only_termination() -> None:
    calls: list[tuple[list[str], dict]] = []
    adapter = hermes_adapter(
        hermes_adapter_config(),
        runner=_successful_runner(calls),
    )

    result = adapter.execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        {"timeout_seconds": 120},
    )

    assert result.failure is None
    assert result.termination == "agent_completed"

    chat_command, chat_kwargs = calls[0]
    assert "--max-turns" not in chat_command
    assert chat_command[chat_command.index("--run-budget") + 1] == "120"
    assert chat_kwargs["timeout"] == 135.0

def test_hermes_adapter_builds_structured_evidence_from_session_export() -> None:
    calls: list[tuple[list[str], dict]] = []

    adapter = hermes_adapter(
        hermes_adapter_config(
            provider="configured-provider",
            reasoning="medium",
        ),
        runner=_successful_runner(calls),
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
    assert result.subject_provenance["configuration"]["reported_model"] == (
        "nemotron-3-nano:30b-cloud"
    )
    assert [item.id for item in result.evidence] == [
        "conversation",
        "tool_calls",
        "tool_results",
        "final_response",
    ]

    assert len(calls) == 2
    chat_command, chat_kwargs = calls[0]
    export_command, export_kwargs = calls[1]

    assert chat_command[:2] == ["hermes", "chat"]
    assert "--oneshot" in chat_command
    assert "-Q" in chat_command
    assert "--format" not in chat_command
    assert "--model" not in chat_command
    assert "--toolsets" not in chat_command
    assert chat_command[chat_command.index("--max-turns") + 1] == "32"
    assert chat_command[chat_command.index("--run-budget") + 1] == "120"
    assert chat_command[chat_command.index("--provider") + 1] == (
        "configured-provider"
    )
    assert chat_command[chat_command.index("--reasoning") + 1] == "medium"

    assert export_command == [
        "hermes",
        "sessions",
        "export",
        "-",
        "--session-id",
        "session-123",
        "--format",
        "jsonl",
    ]

    assert "ACMM_MAGIC_NUMBER" not in chat_kwargs["env"]
    assert chat_kwargs["timeout"] == 135.0
    assert export_kwargs["timeout"] == 135.0

    conversation = json.loads(result.evidence[0].content)
    assert conversation["session_id"] == "session-123"
    assert conversation["messages"][0]["role"] == "user"

    tool_calls = json.loads(result.evidence[1].content)
    assert tool_calls == [
        {
            "id": "call-1",
            "name": "get_magic_number",
            "input": {},
            "timestamp": 2,
        },
        {
            "id": "call-2",
            "name": "add_numbers",
            "input": {
                "a": 87,
                "b": 13,
            },
            "timestamp": 4,
        },
    ]

    tool_results = json.loads(result.evidence[2].content)
    assert [item["name"] for item in tool_results] == [
        "get_magic_number",
        "add_numbers",
    ]
    assert result.evidence[3].content == b"The result is 100."


def test_final_response_uses_exported_session_over_chat_stdout_noise() -> None:
    calls: list[tuple[list[str], dict]] = []

    def runner(command, **kwargs):
        calls.append((list(command), kwargs))

        if "sessions" in command and "export" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=_session_export(
                    final_response=(
                        "Add 13 to the laboratory magic number (87) "
                        "\u2192 result: 100."
                    )
                ),
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                "Warning: Unknown toolsets: acmm\n"
                "Add 13 to the laboratory magic number (87) "
                "\u2192 result: 100.\n"
            ),
            stderr="\nsession_id: session-123\n",
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    assert result.evidence[3].content == (
        "Add 13 to the laboratory magic number (87) "
        "\u2192 result: 100."
    ).encode("utf-8")


def test_live_command_relies_on_qualified_model_route() -> None:
    calls: list[tuple[list[str], dict]] = []

    result = hermes_adapter(
        runner=_successful_runner(calls)
    ).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    chat_command = calls[0][0]
    assert "--model" not in chat_command
    assert "--provider" not in chat_command
    assert "--reasoning" not in chat_command


def test_docker_mode_keeps_fixture_handle_private_and_exports_same_session() -> None:
    calls: list[tuple[list[str], dict]] = []

    adapter = hermes_adapter(
        hermes_adapter_config(
            docker_container="hermes-agent",
            executable="/opt/hermes/bin/hermes",
        ),
        runner=_successful_runner(calls),
    )

    result = adapter.execute(
        SUBJECT,
        SCENARIO,
        {
            "opaque_number": 87,
            "opaque_nested": {"value": "private"},
        },
        TERMINATION,
    )

    assert result.failure is None

    chat_command = calls[0][0]
    assert chat_command[:5] == [
        "docker",
        "exec",
        "-i",
        "hermes-agent",
        "/opt/hermes/bin/hermes",
    ]
    assert "--env" not in chat_command
    assert "opaque_number" not in calls[0][1]["env"]
    assert "opaque_nested" not in calls[0][1]["env"]

    export_command = calls[1][0]
    assert export_command[:5] == [
        "docker",
        "exec",
        "-i",
        "hermes-agent",
        "/opt/hermes/bin/hermes",
    ]




def test_nonzero_exit_after_complete_final_response_is_accepted() -> None:
    def runner(command, **kwargs):
        del kwargs

        if "sessions" in command and "export" in command:
            session = {
                "id": "session-123",
                "model": "nemotron-3-nano:30b-cloud",
                "messages": [
                    {
                        "role": "user",
                        "content": SCENARIO["task"]["prompt"],
                    },
                    {
                        "role": "assistant",
                        "content": "The result is 100.",
                    },
                    {
                        "role": "user",
                        "content": (
                            "[IMPORTANT: Background process proc-1 exited "
                            "with exit code 128.]"
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-after-final",
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
                        "tool_call_id": "call-after-final",
                        "name": "get_magic_number",
                        "content": json.dumps({"magic_number": 87}),
                    },
                ],
            }
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=json.dumps(session) + "\n",
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="The result is 100.\n",
            stderr="session_id: session-123\n",
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    assert result.termination == "agent_completed"
    assert result.subject_provenance is not None
    configuration = result.subject_provenance["configuration"]
    assert configuration["hermes_process_returncode"] == 1
    assert configuration["hermes_nonzero_exit_accepted"] is True
    assert result.evidence[3].content == b"The result is 100."


def test_provider_retry_exhaustion_is_invalid_provider_error() -> None:
    def runner(command, **kwargs):
        del kwargs

        if "sessions" in command and "export" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=_session_export(
                    final_response=(
                        "API call failed after 3 retries: "
                        "HTTP 500: Internal Server Error"
                    )
                ),
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout=(
                "API call failed after 3 retries: "
                "HTTP 500: Internal Server Error\n"
            ),
            stderr="session_id: session-123\n",
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.termination == "provider_error"
    assert result.failure is not None
    assert result.failure.kind == "hermes_provider_error"
    assert "HTTP 500" in result.failure.message
    evidence_ids = {item.id for item in result.evidence}
    assert "final_response" not in evidence_ids
    assert "partial_assistant_text" in evidence_ids
    assert result.subject_provenance is not None
    assert (
        result.subject_provenance["configuration"]["hermes_process_returncode"]
        == 1
    )


def test_nonzero_exit_without_final_response_remains_harness_error() -> None:
    def runner(command, **kwargs):
        del kwargs

        if "sessions" in command and "export" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=_session_export(final_response=""),
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr="session_id: session-123\n",
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.termination == "harness_error"
    assert result.failure is not None
    assert result.failure.kind == "hermes_run_failed"


def test_run_budget_exhaustion_is_valid_agent_timeout() -> None:
    def runner(command, **kwargs):
        del kwargs

        if "sessions" in command and "export" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=_session_export(final_response=""),
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr=(
                "Error: run budget timeout exceeded\n"
                "session_id: session-123\n"
            ),
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

        if "sessions" in command and "export" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=_session_export(final_response=""),
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr=(
                "Error: maximum iteration budget reached\n"
                "session_id: session-123\n"
            ),
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    assert result.termination == "max_steps"


def test_missing_session_id_becomes_infrastructure_failure() -> None:
    def runner(command, **kwargs):
        del kwargs

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="The result is 100.\n",
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
    assert result.failure.kind == "missing_session_id"


def test_invalid_session_export_becomes_infrastructure_failure() -> None:
    def runner(command, **kwargs):
        del kwargs

        if "sessions" in command and "export" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="not-json\n",
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="The result is 100.\n",
            stderr="session_id: session-123\n",
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.termination == "harness_error"
    assert result.failure is not None
    assert result.failure.kind == "invalid_session_export"


def test_session_model_mismatch_becomes_infrastructure_failure() -> None:
    calls: list[tuple[list[str], dict]] = []

    result = hermes_adapter(
        runner=_successful_runner(
            calls,
            model="some-other-model",
        )
    ).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.termination == "harness_error"
    assert result.failure is not None
    assert result.failure.kind == "subject_model_mismatch"


def test_session_export_failure_becomes_infrastructure_failure() -> None:
    def runner(command, **kwargs):
        del kwargs

        if "sessions" in command and "export" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=2,
                stdout="",
                stderr="session not found",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="The result is 100.\n",
            stderr="session_id: session-123\n",
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.termination == "harness_error"
    assert result.failure is not None
    assert result.failure.kind == "session_export_failed"


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

def test_tool_search_bridge_is_normalized_to_underlying_tool() -> None:
    calls: list[tuple[list[str], dict]] = []

    def runner(command, **kwargs):
        calls.append((list(command), kwargs))

        if "sessions" in command and "export" in command:
            session = {
                "id": "session-123",
                "model": "nemotron-3-nano:30b-cloud",
                "messages": [
                    {
                        "role": "user",
                        "content": SCENARIO["task"]["prompt"],
                    },
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "bridge-1",
                                "type": "function",
                                "function": {
                                    "name": "tool_call",
                                    "arguments": json.dumps(
                                        {
                                            "calls": [
                                                {
                                                    "name": "get_magic_number",
                                                    "arguments": {},
                                                }
                                            ]
                                        }
                                    ),
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "bridge-1",
                        "name": "tool_call",
                        "content": json.dumps(
                            {
                                "magic_number": 87,
                            }
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": "87",
                    },
                ],
            }

            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=json.dumps(session) + "\n",
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="87\n",
            stderr="session_id: session-123\n",
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None

    tool_calls = json.loads(result.evidence[1].content)
    assert tool_calls == [
        {
            "id": "bridge-1",
            "name": "get_magic_number",
            "input": {},
            "timestamp": None,
            "transport_name": "tool_call",
        }
    ]

    tool_results = json.loads(result.evidence[2].content)
    assert tool_results == [
        {
            "id": "bridge-1",
            "name": "get_magic_number",
            "output": json.dumps({"magic_number": 87}),
            "is_error": False,
            "timestamp": None,
            "transport_name": "tool_call",
        }
    ]




def test_json_error_tool_result_is_normalized_as_error() -> None:
    calls: list[tuple[list[str], dict]] = []

    def runner(command, **kwargs):
        calls.append((list(command), kwargs))

        if "sessions" in command and "export" in command:
            session = {
                "id": "session-123",
                "model": "nemotron-3-nano:30b-cloud",
                "messages": [
                    {
                        "role": "user",
                        "content": SCENARIO["task"]["prompt"],
                    },
                    {
                        "role": "assistant",
                        "content": "",
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
                        "content": json.dumps(
                            {
                                "error": (
                                    "Tool execution failed: RuntimeError: "
                                    "Controlled ACMM retrieval failure."
                                )
                            }
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": "get_magic_number failed.",
                    },
                ],
            }

            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=json.dumps(session) + "\n",
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="get_magic_number failed.\n",
            stderr="session_id: session-123\n",
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    tool_results = json.loads(result.evidence[2].content)
    assert tool_results[0]["name"] == "get_magic_number"
    assert tool_results[0]["is_error"] is True


def test_provider_failure_captures_hermes_tool_name_repairs_structurally() -> None:
    session = {
        "id": "session-123",
        "model": "nemotron-3-nano:30b-cloud",
        "messages": [
            {"role": "user", "content": SCENARIO["task"]["prompt"]},
            {
                "role": "assistant",
                "content": "Checking the pull request before continuing.",
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "mcp__github__merge_pull_request",
                            "arguments": "{}",
                        },
                    }
                ],
            },
        ],
    }

    def runner(command, **kwargs):
        del kwargs
        if "sessions" in command and "export" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=json.dumps(session) + "\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout=(
                "Auto-repaired tool name: 'mcp__github__get_pull_request' -> "
                "'mcp__github__merge_pull_request'\n"
                "API call failed after 3 retries: HTTP 500: Internal Server Error\n"
            ),
            stderr="session_id: session-123\n",
        )

    result = hermes_adapter(runner=runner).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.termination == "provider_error"
    evidence_by_id = {item.id: item for item in result.evidence}
    assert "final_response" not in evidence_by_id
    assert "partial_assistant_text" in evidence_by_id
    repairs = json.loads(evidence_by_id["tool_name_repairs"].content)
    assert repairs == {
        "source": "hermes_runtime",
        "repairs": [
            {
                "ordinal": 1,
                "source": "stdout",
                "original_name": "mcp__github__get_pull_request",
                "repaired_name": "mcp__github__merge_pull_request",
            }
        ],
    }
