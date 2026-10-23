from __future__ import annotations

import json
from urllib.parse import urlsplit

from acmm.adapters.agent_zero_api import agent_zero_runtime_api_client


_CSRF_TOKEN = "csrf-runtime-token"


class fake_response:
    def __init__(self, payload, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _path(request) -> str:
    return urlsplit(request.full_url).path


def test_runtime_client_reads_external_token_and_updates_plugin_config() -> None:
    requests = []

    def opener(request, *, timeout):
        del timeout
        requests.append(request)
        if _path(request) == "/api/csrf_token":
            return fake_response({"ok": True, "token": _CSRF_TOKEN})
        if _path(request) == "/api/settings_get":
            return fake_response({"settings": {"mcp_server_token": "external-key"}})
        if _path(request) == "/api/plugins":
            return fake_response({"ok": True})
        raise AssertionError(_path(request))

    client = agent_zero_runtime_api_client(
        "http://127.0.0.1:50080/",
        opener=opener,
    )

    token = client.get_external_api_token()
    result = client.update_plugin_config(
        "acmm_tools",
        {
            "magic_number": 87,
            "retrieval_mode": "success",
            "addition_mode": "success",
        },
    )

    assert token == "external-key"
    assert result == {"ok": True}
    protected = [request for request in requests if _path(request) != "/api/csrf_token"]
    assert all(request.get_header("X-csrf-token") == _CSRF_TOKEN for request in protected)
    assert all(request.get_header("Origin") == "http://127.0.0.1:50080" for request in protected)
    plugin_request = protected[-1]
    assert json.loads(plugin_request.data.decode("utf-8")) == {
        "action": "save_config",
        "plugin_name": "acmm_tools",
        "project_name": "",
        "agent_profile": "",
        "settings": {
            "magic_number": 87,
            "retrieval_mode": "success",
            "addition_mode": "success",
        },
    }


def test_runtime_client_creates_context_with_explicit_id() -> None:
    requests = []

    def opener(request, *, timeout):
        del timeout
        requests.append(request)
        if _path(request) == "/api/csrf_token":
            return fake_response({"ok": True, "token": _CSRF_TOKEN})
        return fake_response({"ok": True, "ctxid": "ctx-123"})

    client = agent_zero_runtime_api_client(
        "http://127.0.0.1:50080",
        opener=opener,
    )

    result = client.create_context("ctx-123")
    request = requests[-1]

    assert result["ctxid"] == "ctx-123"
    assert request.full_url.endswith("/api/chat_create")
    assert json.loads(request.data.decode("utf-8")) == {
        "new_context": "ctx-123",
        "project_name": "",
    }


def test_runtime_client_uses_external_api_for_message_logs_and_termination() -> None:
    requests = []

    def opener(request, *, timeout):
        requests.append((request, timeout))
        path = _path(request)
        if path == "/api/api_message":
            return fake_response({"context_id": "ctx-123", "response": "100"})
        if path == "/api/api_log_get":
            return fake_response({"context_id": "ctx-123", "log": {"items": []}})
        if path == "/api/api_terminate_chat":
            return fake_response({"success": True, "context_id": "ctx-123"})
        raise AssertionError(path)

    client = agent_zero_runtime_api_client(
        "http://127.0.0.1:50080",
        opener=opener,
    )

    response = client.send_message(
        "external-key",
        context_id="ctx-123",
        message="Do the task.",
        agent_profile="acmm",
        lifetime_hours=1.0,
        timeout_seconds=135.0,
    )
    logs = client.get_logs("external-key", "ctx-123", length=1000)
    terminated = client.terminate_context("external-key", "ctx-123")

    assert response["response"] == "100"
    assert logs["context_id"] == "ctx-123"
    assert terminated["success"] is True
    assert [request.get_header("X-api-key") for request, _ in requests] == [
        "external-key",
        "external-key",
        "external-key",
    ]
    assert requests[0][1] == 135.0
    assert requests[1][1] == 10.0
    assert json.loads(requests[0][0].data.decode("utf-8")) == {
        "context_id": "ctx-123",
        "message": "Do the task.",
        "attachments": [],
        "lifetime_hours": 1.0,
        "project_name": "",
        "agent_profile": "acmm",
    }
    assert json.loads(requests[1][0].data.decode("utf-8")) == {
        "context_id": "ctx-123",
        "length": 1000,
    }
