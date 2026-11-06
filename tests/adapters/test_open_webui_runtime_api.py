from __future__ import annotations

import json

from acmm.adapters.open_webui_api import open_webui_runtime_api_client


class fake_response:
    def __init__(self, payload, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def read(self) -> bytes:
        if self.payload is None:
            return b"null"
        return json.dumps(self.payload).encode("utf-8")


def test_runtime_client_creates_reads_and_deletes_chat() -> None:
    requests = []
    payloads = [
        {"id": "chat-123"},
        {"chat": {"history": {"messages": {}}}},
        True,
    ]

    def opener(request, *, timeout):
        requests.append((request, timeout))
        return fake_response(payloads.pop(0))

    client = open_webui_runtime_api_client(
        "http://127.0.0.1:3000/",
        opener=opener,
    )

    created = client.create_chat("token", {"chat": {"title": "ACMM run"}})
    fetched = client.get_chat("token", "chat-123")
    deleted = client.delete_chat("token", "chat-123")

    assert created == {"id": "chat-123"}
    assert fetched["chat"]["history"]["messages"] == {}
    assert deleted is True
    assert [request.get_method() for request, _ in requests] == [
        "POST",
        "GET",
        "DELETE",
    ]
    assert requests[0][0].full_url.endswith("/api/v1/chats/new")
    assert requests[1][0].full_url.endswith("/api/v1/chats/chat-123")
    assert requests[2][0].full_url.endswith("/api/v1/chats/chat-123")


def test_runtime_client_allows_null_blocking_completion_response() -> None:
    captured = {}

    def opener(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return fake_response(None)

    client = open_webui_runtime_api_client(
        "http://127.0.0.1:3000",
        opener=opener,
    )

    result = client.complete_chat(
        "token",
        {
            "model": "nemotron-3-nano:30b-cloud",
            "stream": True,
            "chat_id": "chat-123",
            "id": "assistant-123",
        },
        timeout_seconds=135.0,
    )

    assert result is None
    assert captured["timeout"] == 135.0
    assert captured["request"].full_url.endswith("/api/chat/completions")
    assert captured["request"].get_header("Authorization") == "Bearer token"


def test_runtime_client_lists_available_tool_ids() -> None:
    requests = []

    def opener(request, *, timeout):
        requests.append((request, timeout))
        return fake_response([{"id": "server:mcp:github"}])

    client = open_webui_runtime_api_client(
        "http://127.0.0.1:3000",
        opener=opener,
    )

    result = client.list_tools("token")

    assert result == [{"id": "server:mcp:github"}]
    assert requests[0][0].full_url.endswith("/api/v1/tools/")
    assert requests[0][0].get_method() == "GET"
    assert requests[0][0].get_header("Authorization") == "Bearer token"
