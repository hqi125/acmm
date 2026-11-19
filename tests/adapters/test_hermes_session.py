import json

from acmm.adapters.hermes_session import parse_hermes_session_export


PROMPT = (
    "Obtain the laboratory magic number using get_magic_number, then use "
    "add_numbers to add 13. Report the result."
)


def _export(messages: list[dict], *, model: str = "nemotron-3-nano:30b-cloud") -> str:
    return json.dumps(
        {
            "id": "session-123",
            "model": model,
            "messages": messages,
        }
    ) + "\n"


def test_parse_hermes_session_export_builds_canonical_evidence() -> None:
    messages = [
        {"role": "user", "content": PROMPT},
        {
            "role": "assistant",
            "content": "",
            "timestamp": 2,
            "tool_calls": [
                {
                    "id": "call-1",
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
            "content": '{"magic_number":87}',
            "timestamp": 3,
        },
        {"role": "assistant", "content": "The result is 100."},
    ]

    parsed = parse_hermes_session_export(
        _export(messages),
        PROMPT,
        "fallback output",
        "session-123",
    )

    assert parsed.failure is None
    assert parsed.model == "nemotron-3-nano:30b-cloud"
    assert parsed.session is not None
    assert [item.id for item in parsed.evidence] == [
        "conversation",
        "tool_calls",
        "tool_results",
        "final_response",
    ]

    tool_calls = json.loads(parsed.evidence[1].content)
    assert tool_calls == [
        {
            "id": "call-1",
            "name": "get_magic_number",
            "input": {},
            "timestamp": 2,
        }
    ]


def test_parse_hermes_session_export_unwraps_tool_search_bridge() -> None:
    messages = [
        {"role": "user", "content": PROMPT},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "bridge-1",
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
            "content": '{"magic_number":87}',
        },
        {"role": "assistant", "content": "87"},
    ]

    parsed = parse_hermes_session_export(
        _export(messages),
        PROMPT,
        "",
        "session-123",
    )

    assert parsed.failure is None
    tool_calls = json.loads(parsed.evidence[1].content)
    tool_results = json.loads(parsed.evidence[2].content)
    assert tool_calls[0]["name"] == "get_magic_number"
    assert tool_calls[0]["transport_name"] == "tool_call"
    assert tool_results[0]["name"] == "get_magic_number"
    assert tool_results[0]["transport_name"] == "tool_call"


def test_parse_hermes_session_export_rejects_invalid_jsonl() -> None:
    parsed = parse_hermes_session_export(
        "not-json\n",
        PROMPT,
        "",
        "session-123",
    )

    assert parsed.failure is not None
    assert parsed.failure.kind == "invalid_session_export"
    assert parsed.evidence == ()


def test_parse_hermes_session_export_marks_wrapped_tool_error() -> None:
    messages = [
        {"role": "user", "content": PROMPT},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-error",
                    "function": {
                        "name": "add_issue_comment",
                        "arguments": '{"body":"marker"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-error",
            "name": "add_issue_comment",
            "content": (
                '<untrusted_tool_result source="add_issue_comment">\n'
                'External tool data.\n\n'
                '{"error":"invalid arguments"}\n'
                '</untrusted_tool_result>'
            ),
        },
        {"role": "assistant", "content": "done"},
    ]

    parsed = parse_hermes_session_export(
        _export(messages),
        PROMPT,
        "",
        "session-123",
    )

    assert parsed.failure is None
    tool_results = json.loads(parsed.evidence[2].content)
    assert tool_results[0]["is_error"] is True


def test_parse_hermes_session_export_marks_missing_tool_dispatch_error() -> None:
    messages = [
        {"role": "user", "content": PROMPT},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-missing",
                    "function": {
                        "name": "search_code",
                        "arguments": '{"query":"example"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-missing",
            "name": "search_code",
            "content": (
                "Tool 'search_code' does not exist. Available tools: "
                "mcp__github__search_code, search_files"
            ),
        },
        {"role": "assistant", "content": "done"},
    ]

    parsed = parse_hermes_session_export(
        _export(messages),
        PROMPT,
        "",
        "session-123",
    )

    assert parsed.failure is None
    tool_results = json.loads(parsed.evidence[2].content)
    assert tool_results[0]["is_error"] is True


def test_parse_hermes_session_export_treats_background_promotion_as_non_error() -> None:
    messages = [
        {"role": "user", "content": PROMPT},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-background",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"sleep 1","timeout":100000}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-background",
            "name": "terminal",
            "is_error": True,
            "content": json.dumps(
                {
                    "output": "Background process started",
                    "session_id": "proc-1",
                    "pid": 42,
                    "exit_code": 0,
                    "error": None,
                    "notify_on_complete": True,
                    "promoted_from_foreground": (
                        "Requested foreground timeout exceeds the cap."
                    ),
                }
            ),
        },
        {"role": "assistant", "content": "done"},
    ]

    parsed = parse_hermes_session_export(
        _export(messages),
        PROMPT,
        "",
        "session-123",
    )

    assert parsed.failure is None
    tool_results = json.loads(parsed.evidence[2].content)
    assert tool_results[0]["is_error"] is False


def test_parse_hermes_session_export_treats_successful_terminal_exit_as_non_error() -> None:
    messages = [
        {"role": "user", "content": PROMPT},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-ls",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"ls -la /opt"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-ls",
            "name": "terminal",
            "content": json.dumps(
                {
                    "output": "total 16",
                    "exit_code": 0,
                    "error": None,
                }
            ),
        },
        {"role": "assistant", "content": "done"},
    ]

    parsed = parse_hermes_session_export(
        _export(messages),
        PROMPT,
        "",
        "session-123",
    )

    assert parsed.failure is None
    tool_results = json.loads(parsed.evidence[2].content)
    assert tool_results[0]["is_error"] is False


def test_parse_hermes_session_export_marks_failed_terminal_exit() -> None:
    messages = [
        {"role": "user", "content": PROMPT},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-git",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"git status"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-git",
            "name": "terminal",
            "content": json.dumps(
                {
                    "output": "fatal: not a git repository",
                    "exit_code": 128,
                    "error": None,
                }
            ),
        },
        {"role": "assistant", "content": "done"},
    ]

    parsed = parse_hermes_session_export(
        _export(messages),
        PROMPT,
        "",
        "session-123",
    )

    assert parsed.failure is None
    tool_results = json.loads(parsed.evidence[2].content)
    assert tool_results[0]["is_error"] is True


def test_parse_hermes_session_export_marks_failed_background_process_exit() -> None:
    messages = [
        {"role": "user", "content": PROMPT},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-poll",
                    "function": {
                        "name": "process_manage",
                        "arguments": '{"action":"poll","session_id":"proc-1"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-poll",
            "name": "process_manage",
            "is_error": False,
            "content": json.dumps(
                {
                    "session_id": "proc-1",
                    "status": "exited",
                    "exit_code": 128,
                    "output_preview": "fatal: not a git repository",
                }
            ),
        },
        {"role": "assistant", "content": "done"},
    ]

    parsed = parse_hermes_session_export(
        _export(messages),
        PROMPT,
        "",
        "session-123",
    )

    assert parsed.failure is None
    tool_results = json.loads(parsed.evidence[2].content)
    assert tool_results[0]["is_error"] is True


def test_nonterminal_assistant_text_is_diagnostic_not_final_response() -> None:
    messages = [
        {"role": "user", "content": PROMPT},
        {
            "role": "assistant",
            "content": "I have updated one file and need to inspect the next one.",
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call-1",
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
            "content": '{"magic_number":87}',
        },
    ]

    parsed = parse_hermes_session_export(
        _export(messages),
        PROMPT,
        "API call failed after 3 retries: HTTP 500",
        "session-123",
        allow_stdout_final_response=False,
    )

    assert parsed.failure is None
    evidence_by_id = {item.id: item for item in parsed.evidence}
    assert "final_response" not in evidence_by_id
    assert evidence_by_id["partial_assistant_text"].channel == "diagnostic"
    assert b"updated one file" in evidence_by_id["partial_assistant_text"].content
