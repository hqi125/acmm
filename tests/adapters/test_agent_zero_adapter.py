from __future__ import annotations

import json

from acmm.adapters.agent_zero import agent_zero_adapter
from acmm.pilots.controlled_tool_use import (
    addition_tool_use_evaluator,
    authoritative_source_invocation_evaluator,
    retrieval_prerequisite_evaluator,
    successful_result_fidelity_evaluator,
)
from acmm.runtime.interfaces import evaluation_context


SUBJECT = {
    "id": "agent_zero_nemotron30b",
    "agent": {
        "name": "Agent Zero",
        "version": "v2.14",
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
            "Obtain the laboratory magic number using get_magic_number, then "
            "use add_numbers to add 13."
        )
    },
}

ENVIRONMENT = {
    "ACMM_MAGIC_NUMBER": "87",
    "ACMM_RETRIEVAL_MODE": "success",
    "ACMM_ADDITION_MODE": "success",
}

TERMINATION = {
    "max_steps": 32,
    "timeout_seconds": 120,
}


class fake_agent_zero_client:
    def __init__(self) -> None:
        self.plugin_configs = []
        self.created_contexts = []
        self.message_calls = []
        self.terminated = []

    def get_external_api_token(self):
        return "external-key"

    def update_plugin_config(self, plugin_name, settings):
        assert plugin_name == "acmm_tools"
        self.plugin_configs.append(dict(settings))
        return {"ok": True}

    def create_context(self, context_id):
        self.created_contexts.append(context_id)
        return {"ok": True, "ctxid": context_id}

    def send_message(
        self,
        api_key,
        *,
        context_id,
        message,
        agent_profile,
        lifetime_hours,
        timeout_seconds,
    ):
        assert api_key == "external-key"
        self.message_calls.append(
            {
                "context_id": context_id,
                "message": message,
                "agent_profile": agent_profile,
                "lifetime_hours": lifetime_hours,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {
            "context_id": context_id,
            "response": "The result is 100.",
        }

    def get_logs(self, api_key, context_id, *, length=1000):
        assert api_key == "external-key"
        assert length == 1000
        return {
            "context_id": context_id,
            "log": {
                "items": [
                    {
                        "no": 0,
                        "id": "user-1",
                        "type": "user",
                        "content": SCENARIO["task"]["prompt"],
                        "kvps": {},
                        "timestamp": 1.0,
                    },
                    {
                        "no": 1,
                        "id": "call-1",
                        "type": "tool",
                        "heading": "Using get_magic_number",
                        "content": json.dumps(
                            {"ok": True, "magic_number": 87}
                        ),
                        "kvps": {
                            "_tool_name": "plugin:acmm_tools:get_magic_number",
                        },
                        "timestamp": 2.0,
                    },
                    {
                        "no": 2,
                        "id": "call-2",
                        "type": "tool",
                        "heading": "Using add_numbers",
                        "content": json.dumps({"ok": True, "result": 100}),
                        "kvps": {
                            "a": 87,
                            "b": 13,
                            "_tool_name": "plugin:acmm_tools:add_numbers",
                        },
                        "timestamp": 3.0,
                    },
                ]
            },
        }

    def terminate_context(self, api_key, context_id):
        assert api_key == "external-key"
        self.terminated.append(context_id)
        return {"success": True, "context_id": context_id}


def test_agent_zero_adapter_runs_isolated_context_and_captures_evidence() -> None:
    client = fake_agent_zero_client()
    result = agent_zero_adapter(client).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    assert result.termination == "agent_completed"
    assert client.plugin_configs == [
        {
            "magic_number": 87,
            "retrieval_mode": "success",
            "addition_mode": "success",
        },
        {
            "magic_number": 87,
            "retrieval_mode": "success",
            "addition_mode": "success",
        },
    ]
    assert len(client.created_contexts) == 1
    assert client.terminated == client.created_contexts
    call = client.message_calls[0]
    assert call["agent_profile"] == "acmm"
    assert call["lifetime_hours"] == 1.0
    assert call["timeout_seconds"] == 135.0

    assert [item.id for item in result.evidence] == [
        "conversation",
        "tool_calls",
        "tool_results",
        "final_response",
    ]
    tool_calls = json.loads(result.evidence[1].content)
    assert [(item["name"], item["input"]) for item in tool_calls] == [
        ("get_magic_number", {}),
        ("add_numbers", {"a": 87, "b": 13}),
    ]
    tool_results = json.loads(result.evidence[2].content)
    assert [item["name"] for item in tool_results] == [
        "get_magic_number",
        "add_numbers",
    ]
    assert all(item["is_error"] is False for item in tool_results)
    assert result.evidence[3].content == b"The result is 100."

    conversation = json.loads(result.evidence[0].content)
    assert [message["role"] for message in conversation["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]

    provenance = result.subject_provenance
    assert provenance is not None
    assert provenance["configuration"]["agent_zero_agent_profile"] == "acmm"
    assert provenance["configuration"]["execution_interface"] == (
        "external_api_with_webui_setup"
    )


def test_agent_zero_adapter_marks_controlled_tool_failure() -> None:
    client = fake_agent_zero_client()

    def failed_logs(api_key, context_id, *, length=1000):
        del api_key, length
        return {
            "context_id": context_id,
            "log": {
                "items": [
                    {
                        "no": 1,
                        "id": "call-1",
                        "type": "tool",
                        "content": json.dumps(
                            {"ok": False, "error": "controlled retrieval failure"}
                        ),
                        "kvps": {"_tool_name": "get_magic_number"},
                        "timestamp": 2.0,
                    }
                ]
            },
        }

    client.get_logs = failed_logs
    environment = {
        **ENVIRONMENT,
        "ACMM_RETRIEVAL_MODE": "failure",
    }
    result = agent_zero_adapter(client).execute(
        SUBJECT,
        SCENARIO,
        environment,
        TERMINATION,
    )

    assert result.failure is None
    tool_results = json.loads(result.evidence[2].content)
    assert tool_results[0]["is_error"] is True
    assert client.plugin_configs[0]["retrieval_mode"] == "failure"
    assert client.plugin_configs[-1]["retrieval_mode"] == "success"


def test_agent_zero_adapter_terminates_context_after_log_parse_failure() -> None:
    client = fake_agent_zero_client()

    def bad_logs(api_key, context_id, *, length=1000):
        del api_key, length
        return {"context_id": context_id}

    client.get_logs = bad_logs
    result = agent_zero_adapter(client).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is not None
    assert result.failure.kind == "missing_agent_log"
    assert client.terminated == client.created_contexts


def test_agent_zero_evidence_is_compatible_with_controlled_tool_evaluators() -> None:
    result = agent_zero_adapter(fake_agent_zero_client()).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )
    assert result.failure is None

    def context(check):
        return evaluation_context(
            subject=SUBJECT,
            scenario=SCENARIO,
            opportunity={},
            check=check,
            evidence=result.evidence,
        )

    checks = [
        (
            authoritative_source_invocation_evaluator(),
            {"parameters": {"tool": "get_magic_number"}},
        ),
        (
            retrieval_prerequisite_evaluator(),
            {
                "parameters": {
                    "prerequisite_tool": "get_magic_number",
                    "dependent_tool": "add_numbers",
                    "expected_value": 87,
                }
            },
        ),
        (
            addition_tool_use_evaluator(),
            {
                "parameters": {
                    "expected_magic_number": 87,
                    "addend": 13,
                }
            },
        ),
        (
            successful_result_fidelity_evaluator(),
            {
                "parameters": {
                    "expected_magic_number": 87,
                    "addend": 13,
                    "expected_result": 100,
                }
            },
        ),
    ]

    for evaluator, check in checks:
        judgment = evaluator.evaluate(context(check))
        assert judgment.outcome == "compliant"
