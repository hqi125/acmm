from __future__ import annotations

import json

from acmm.adapters.agent_zero import agent_zero_adapter


SUBJECT = {
    "id": "agent_zero_gpt_oss120b",
    "agent": {
        "name": "Agent Zero",
        "version": "v2.14",
    },
    "model": {
        "name": "gpt-oss:120b-cloud",
        "provider": "ollama_cloud",
    },
    "configuration": {
        "context_window": 64000,
    },
}

SCENARIO = {
    "id": "lookup_and_sum",
    "task": {
        "prompt": (
            "Look up the controlled record value, then add 13 using the "
            "available native tools."
        )
    },
}

ENVIRONMENT = {}

TERMINATION = {
    "max_steps": 32,
    "timeout_seconds": 120,
}


class fake_agent_zero_client:
    def __init__(self) -> None:
        self.created_contexts = []
        self.message_calls = []
        self.terminated = []

    def get_external_api_token(self):
        return "external-key"

    def get_model_config(self, *, agent_profile):
        assert agent_profile == "acmm"
        return {
            "configured_preset": "Default",
            "selected_preset": "Default",
            "config": {
                "chat_model": {
                    "provider": "ollama",
                    "name": "gpt-oss:120b-cloud",
                    "api_base": "http://host.docker.internal:11434",
                    "ctx_length": 64000,
                },
                "utility_model": {
                    "provider": "ollama",
                    "name": "gpt-oss:120b-cloud",
                    "api_base": "http://host.docker.internal:11434",
                    "ctx_length": 64000,
                },
            },
        }


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
                        "heading": "Using lookup_record",
                        "content": json.dumps(
                            {"ok": True, "value": 87}
                        ),
                        "kvps": {
                            "_tool_name": "plugin:example_tools:lookup_record",
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
                            "_tool_name": "plugin:example_tools:add_numbers",
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
    assert len(client.created_contexts) == 1
    assert client.terminated == client.created_contexts
    call = client.message_calls[0]
    assert call["agent_profile"] == "acmm"
    assert call["lifetime_hours"] == 1.0
    assert call["timeout_seconds"] == 120.0

    assert [item.id for item in result.evidence] == [
        "conversation",
        "tool_calls",
        "tool_results",
        "final_response",
    ]
    tool_calls = json.loads(result.evidence[1].content)
    assert [(item["name"], item["input"]) for item in tool_calls] == [
        ("plugin:example_tools:lookup_record", {}),
        ("plugin:example_tools:add_numbers", {"a": 87, "b": 13}),
    ]
    tool_results = json.loads(result.evidence[2].content)
    assert [item["name"] for item in tool_results] == [
        "plugin:example_tools:lookup_record",
        "plugin:example_tools:add_numbers",
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
    assert provenance["configuration"]["reported_model"] == (
        "gpt-oss:120b-cloud"
    )
    assert provenance["configuration"]["reported_context_window"] == 64000
    assert provenance["configuration"]["reported_model_source"] == (
        "agent_zero_model_config_api"
    )


def test_agent_zero_adapter_rejects_observed_model_mismatch() -> None:
    client = fake_agent_zero_client()

    def wrong_model(*, agent_profile):
        assert agent_profile == "acmm"
        response = client.__class__.get_model_config(client, agent_profile=agent_profile)
        response["config"]["chat_model"]["name"] = "different-model"
        return response

    client.get_model_config = wrong_model
    result = agent_zero_adapter(client).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.termination == "harness_error"
    assert result.failure is not None
    assert result.failure.kind == "subject_model_mismatch"
    assert result.subject_provenance is not None
    assert result.subject_provenance["configuration"]["reported_model"] == (
        "different-model"
    )


def test_agent_zero_adapter_rejects_observed_context_mismatch() -> None:
    client = fake_agent_zero_client()

    def wrong_context(*, agent_profile):
        assert agent_profile == "acmm"
        response = client.__class__.get_model_config(client, agent_profile=agent_profile)
        response["config"]["chat_model"]["ctx_length"] = 32768
        return response

    client.get_model_config = wrong_context
    result = agent_zero_adapter(client).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.termination == "harness_error"
    assert result.failure is not None
    assert result.failure.kind == "subject_context_mismatch"



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
                        "kvps": {"_tool_name": "lookup_record"},
                        "timestamp": 2.0,
                    }
                ]
            },
        }

    client.get_logs = failed_logs
    environment = {
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



def test_agent_zero_timeout_captures_partial_evidence_before_cleanup() -> None:
    client = fake_agent_zero_client()

    def timeout_message(*args, **kwargs):
        del args, kwargs
        raise TimeoutError("controlled timeout")

    def partial_logs(api_key, context_id, *, length=1000):
        assert api_key == "external-key"
        assert length == 1000
        return {
            "context_id": context_id,
            "log": {
                "items": [
                    {
                        "no": 1,
                        "id": "call-1",
                        "type": "tool",
                        "content": json.dumps(
                            {"ok": True, "value": 87}
                        ),
                        "kvps": {
                            "_tool_name": "plugin:example_tools:lookup_record",
                        },
                        "timestamp": 2.0,
                    }
                ]
            },
        }

    client.send_message = timeout_message
    client.get_logs = partial_logs

    result = agent_zero_adapter(client).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        {"timeout_seconds": 120},
    )

    assert result.failure is None
    assert result.termination == "timeout"
    assert [item.id for item in result.evidence] == [
        "conversation",
        "tool_calls",
        "tool_results",
        "final_response",
    ]
    tool_calls = json.loads(result.evidence[1].content)
    assert [(item["name"], item["input"]) for item in tool_calls] == [
        ("plugin:example_tools:lookup_record", {}),
    ]
    assert result.evidence[3].content == b""
    assert client.terminated == client.created_contexts


def test_agent_zero_timeout_with_unreadable_log_is_harness_error() -> None:
    client = fake_agent_zero_client()

    def timeout_message(*args, **kwargs):
        del args, kwargs
        raise TimeoutError("controlled timeout")

    def bad_logs(api_key, context_id, *, length=1000):
        del api_key, length
        return {"context_id": context_id}

    client.send_message = timeout_message
    client.get_logs = bad_logs

    result = agent_zero_adapter(client).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        {"timeout_seconds": 120},
    )

    assert result.termination == "harness_error"
    assert result.failure is not None
    assert result.failure.kind == "missing_agent_log"
    assert client.terminated == client.created_contexts

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
