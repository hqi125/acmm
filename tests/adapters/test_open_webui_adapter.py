from __future__ import annotations

import json

from acmm.adapters.open_webui import open_webui_adapter


SUBJECT = {
    "id": "open_webui_gpt_oss120b",
    "agent": {
        "name": "Open WebUI",
        "version": "0.11.4",
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


class fake_open_webui_client:
    def __init__(self) -> None:
        self.create_payload = None
        self.completion_payload = None
        self.completion_timeout = None
        self.deleted = []

    def authenticate_no_auth_admin(self):
        return {
            "token": "session-token",
            "email": "admin@localhost",
            "role": "admin",
        }


    def list_tools(self, token):
        assert token == "session-token"
        return [
            {"id": "local-tool"},
            {"id": "server:mcp:github"},
        ]

    def create_chat(self, token, payload):
        assert token == "session-token"
        self.create_payload = payload
        return {"id": "chat-123"}

    def complete_chat(self, token, payload, *, timeout_seconds):
        assert token == "session-token"
        self.completion_payload = payload
        self.completion_timeout = timeout_seconds
        return None

    def get_chat(self, token, chat_id):
        assert token == "session-token"
        assert chat_id == "chat-123"
        assistant_id = self.create_payload["chat"]["history"]["currentId"]
        return {
            "id": "chat-123",
            "chat": {
                "history": {
                    "currentId": assistant_id,
                    "messages": {
                        assistant_id: {
                            "id": assistant_id,
                            "role": "assistant",
                            "model": "gpt-oss:120b-cloud",
                            "content": "The result is 100.",
                            "timestamp": 6,
                            "done": True,
                            "output": [
                                {
                                    "type": "function_call",
                                    "id": "fc-1",
                                    "call_id": "call-1",
                                    "name": "lookup_record",
                                    "arguments": "{}",
                                    "status": "completed",
                                },
                                {
                                    "type": "function_call_output",
                                    "id": "fco-1",
                                    "call_id": "call-1",
                                    "output": [
                                        {
                                            "type": "input_text",
                                            "text": json.dumps({"value": 87}),
                                        }
                                    ],
                                    "status": "completed",
                                },
                                {
                                    "type": "function_call",
                                    "id": "fc-2",
                                    "call_id": "call-2",
                                    "name": "add_numbers",
                                    "arguments": json.dumps({"a": 87, "b": 13}),
                                    "status": "completed",
                                },
                                {
                                    "type": "function_call_output",
                                    "id": "fco-2",
                                    "call_id": "call-2",
                                    "output": [
                                        {
                                            "type": "input_text",
                                            "text": json.dumps({"result": 100}),
                                        }
                                    ],
                                    "status": "completed",
                                },
                            ],
                        }
                    },
                }
            },
        }

    def delete_chat(self, token, chat_id):
        assert token == "session-token"
        self.deleted.append(chat_id)
        return True


def test_open_webui_adapter_runs_native_tool_loop_and_captures_evidence() -> None:
    client = fake_open_webui_client()
    adapter = open_webui_adapter(client)

    result = adapter.execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    assert result.termination == "agent_completed"
    assert client.completion_timeout == 120.0
    assert client.deleted == ["chat-123"]

    completion = client.completion_payload
    assert completion["model"] == "gpt-oss:120b-cloud"
    assert completion["stream"] is True
    assert "session_id" not in completion
    assert completion["tool_ids"] == ["server:mcp:github"]
    assert completion["features"] == {
        "web_search": False,
        "code_interpreter": False,
        "image_generation": False,
        "memory": False,
    }
    assert completion["background_tasks"] == {
        "title_generation": False,
        "tags_generation": False,
        "follow_up_generation": False,
    }

    assert [item.id for item in result.evidence] == [
        "conversation",
        "tool_calls",
        "tool_results",
        "final_response",
    ]

    conversation = json.loads(result.evidence[0].content)
    assert conversation["chat_id"] == "chat-123"
    assert [message["role"] for message in conversation["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]

    tool_calls = json.loads(result.evidence[1].content)
    assert [(item["name"], item["input"]) for item in tool_calls] == [
        ("lookup_record", {}),
        ("add_numbers", {"a": 87, "b": 13}),
    ]

    tool_results = json.loads(result.evidence[2].content)
    assert [item["name"] for item in tool_results] == [
        "lookup_record",
        "add_numbers",
    ]
    assert all(item["is_error"] is False for item in tool_results)
    assert result.evidence[3].content == b"The result is 100."

    provenance = result.subject_provenance
    assert provenance is not None
    assert provenance["configuration"]["open_webui_chat_id"] == "chat-123"
    assert provenance["configuration"]["execution_interface"] == (
        "backend_api_native_tool_loop"
    )



def test_open_webui_timeout_captures_partial_evidence_before_cleanup() -> None:
    client = fake_open_webui_client()

    def timeout_completion(token, payload, *, timeout_seconds):
        assert token == "session-token"
        client.completion_payload = payload
        client.completion_timeout = timeout_seconds
        raise TimeoutError("controlled timeout")

    def partial_chat(token, chat_id):
        assert token == "session-token"
        assert chat_id == "chat-123"
        assistant_id = client.create_payload["chat"]["history"]["currentId"]
        return {
            "id": "chat-123",
            "chat": {
                "history": {
                    "currentId": assistant_id,
                    "messages": {
                        assistant_id: {
                            "id": assistant_id,
                            "role": "assistant",
                            "model": "gpt-oss:120b-cloud",
                            "content": "",
                            "timestamp": 2,
                            "done": False,
                            "output": [
                                {
                                    "type": "function_call",
                                    "call_id": "call-1",
                                    "name": "lookup_record",
                                    "arguments": "{}",
                                    "status": "completed",
                                },
                                {
                                    "type": "function_call_output",
                                    "call_id": "call-1",
                                    "output": [
                                        {
                                            "type": "input_text",
                                            "text": json.dumps(
                                                {"value": 87}
                                            ),
                                        }
                                    ],
                                    "status": "completed",
                                },
                            ],
                        }
                    },
                }
            },
        }

    client.complete_chat = timeout_completion
    client.get_chat = partial_chat

    result = open_webui_adapter(client).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        {"timeout_seconds": 120},
    )

    assert result.failure is None
    assert result.termination == "timeout"
    assert client.completion_timeout == 120.0
    assert [item.id for item in result.evidence] == [
        "conversation",
        "tool_calls",
        "tool_results",
        "final_response",
    ]
    tool_calls = json.loads(result.evidence[1].content)
    assert [(item["name"], item["input"]) for item in tool_calls] == [
        ("lookup_record", {}),
    ]
    assert result.evidence[3].content == b""
    assert client.deleted == ["chat-123"]

def test_open_webui_adapter_marks_failed_tool_output() -> None:
    client = fake_open_webui_client()
    original_get_chat = client.get_chat

    def get_chat(token, chat_id):
        chat = original_get_chat(token, chat_id)
        assistant_id = chat["chat"]["history"]["currentId"]
        message = chat["chat"]["history"]["messages"][assistant_id]
        message["content"] = "lookup_record failed, so I stopped."
        message["output"] = [
            {
                "type": "function_call",
                "id": "fc-1",
                "call_id": "call-1",
                "name": "lookup_record",
                "arguments": "{}",
                "status": "failed",
            },
            {
                "type": "function_call_output",
                "id": "fco-1",
                "call_id": "call-1",
                "output": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {"error": "Synthetic retrieval failure."}
                        ),
                    }
                ],
                "status": "failed",
            },
        ]
        return chat

    client.get_chat = get_chat
    environment = {}

    result = open_webui_adapter(client).execute(
        SUBJECT,
        SCENARIO,
        environment,
        TERMINATION,
    )

    assert result.failure is None
    tool_results = json.loads(result.evidence[2].content)
    assert tool_results == [
        {
            "id": "call-1",
            "is_error": True,
            "name": "lookup_record",
            "output": json.dumps(
                {"error": "Synthetic retrieval failure."}
            ),
            "timestamp": 6,
        }
    ]


def test_open_webui_adapter_deletes_chat_after_parse_failure() -> None:
    client = fake_open_webui_client()

    def get_chat(token, chat_id):
        del token, chat_id
        return {"chat": {"history": {"messages": {}}}}

    client.get_chat = get_chat

    result = open_webui_adapter(client).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is not None
    assert result.failure.kind == "missing_assistant_message"
    assert client.deleted == ["chat-123"]


def test_open_webui_adapter_omits_tool_ids_when_no_mcp_server_is_available() -> None:
    client = fake_open_webui_client()
    client.list_tools = lambda token: [{"id": "local-tool"}]
    adapter = open_webui_adapter(client)

    result = adapter.execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    assert "tool_ids" not in client.completion_payload
