from __future__ import annotations

import json

from acmm.adapters.agent_zero import agent_zero_adapter
from acmm.adapters.open_webui import open_webui_adapter


SUBJECT = {
    "id": "test_subject",
    "agent": {"name": "Test", "version": "1"},
    "model": {"name": "gpt-oss:120b-cloud", "provider": "ollama_cloud"},
    "configuration": {},
}
SCENARIO = {"id": "test", "task": {"prompt": "test"}}
TERMINATION = {"timeout_seconds": 120}
CONFIG = {
    "workspace_id": "workspace-test",
    "source_value": 41,
    "target_value": 0,
    "protected_value": 99,
    "source_read_mode": "success",
    "target_write_mode": "success",
    "distractor_mode": "none",
}
BASELINE = {
    "workspace_id": "baseline",
    "source_value": 0,
    "target_value": 0,
    "protected_value": 0,
    "source_read_mode": "success",
    "target_write_mode": "success",
    "distractor_mode": "none",
}
ENVIRONMENT = {
    "ACMM_TOOL_CONFIG_JSON": json.dumps(CONFIG),
    "ACMM_TOOL_BASELINE_JSON": json.dumps(BASELINE),
}


class _open_webui_client:
    def __init__(self) -> None:
        self.valves = None
        self.created = None

    def authenticate_no_auth_admin(self):
        return {"token": "token"}

    def update_tool_valves(self, token, tool_id, valves):
        del token, tool_id
        self.valves = dict(valves)
        return dict(valves)

    def create_chat(self, token, payload):
        del token
        self.created = payload
        return {"id": "chat"}

    def complete_chat(self, token, payload, *, timeout_seconds):
        del token, payload, timeout_seconds

    def get_chat(self, token, chat_id):
        del token, chat_id
        assistant_id = self.created["chat"]["history"]["currentId"]
        return {
            "id": "chat",
            "chat": {
                "history": {
                    "currentId": assistant_id,
                    "messages": {
                        assistant_id: {
                            "id": assistant_id,
                            "role": "assistant",
                            "model": "gpt-oss:120b-cloud",
                            "content": "done",
                            "done": True,
                            "output": [],
                        }
                    },
                }
            },
        }

    def delete_chat(self, token, chat_id):
        del token, chat_id
        return True


class _agent_zero_client:
    def __init__(self) -> None:
        self.configs = []

    def get_external_api_token(self):
        return "key"

    def get_model_config(self, *, agent_profile):
        assert agent_profile == "acmm"
        return {
            "config": {
                "chat_model": {
                    "name": "gpt-oss:120b-cloud",
                    "ctx_length": 64000,
                },
                "utility_model": {
                    "name": "gpt-oss:120b-cloud",
                    "ctx_length": 64000,
                },
            }
        }

    def update_plugin_config(self, plugin_name, settings):
        del plugin_name
        self.configs.append(dict(settings))
        return {"ok": True}

    def create_context(self, context_id):
        return {"ctxid": context_id}

    def send_message(self, api_key, **kwargs):
        del api_key
        return {"context_id": kwargs["context_id"], "response": "done"}

    def get_logs(self, api_key, context_id, *, length=1000):
        del api_key, length
        return {
            "context_id": context_id,
            "log": {
                "items": [
                    {
                        "id": "read-1",
                        "type": "tool",
                        "content": json.dumps(
                            {"ok": True, "record_id": "source", "value": 41}
                        ),
                        "kvps": {
                            "record_id": "source",
                            "_tool_name": "plugin:acmm_tools:read_record",
                        },
                    },
                    {
                        "id": "write-1",
                        "type": "tool",
                        "content": json.dumps(
                            {
                                "ok": True,
                                "record_id": "target",
                                "value": 41,
                                "written": True,
                                "observed_value": 41,
                            }
                        ),
                        "kvps": {
                            "record_id": "target",
                            "value": 41,
                            "_tool_name": "plugin:acmm_tools:write_record",
                        },
                    },
                ]
            },
        }

    def terminate_context(self, api_key, context_id):
        del api_key
        return {"context_id": context_id, "success": True}


def test_open_webui_adapter_accepts_generic_tool_configuration() -> None:
    client = _open_webui_client()
    result = open_webui_adapter(client).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    assert client.valves == CONFIG


def test_agent_zero_adapter_accepts_and_restores_generic_configuration() -> None:
    client = _agent_zero_client()
    result = agent_zero_adapter(client).execute(
        SUBJECT,
        SCENARIO,
        ENVIRONMENT,
        TERMINATION,
    )

    assert result.failure is None
    assert client.configs == [CONFIG, BASELINE]
    evidence = {item.id: item for item in result.evidence}
    calls = json.loads(evidence["tool_calls"].content)
    assert [call["name"] for call in calls] == ["read_record", "write_record"]
