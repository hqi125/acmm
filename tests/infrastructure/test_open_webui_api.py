from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from acmm_infra.subjects.open_webui.api import open_webui_api_client, open_webui_api_error


class fake_response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_authenticate_no_auth_admin_posts_system_credentials() -> None:
    captured = {}

    def opener(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return fake_response(
            {
                "token": "session-token",
                "email": "admin@localhost",
                "role": "admin",
            }
        )

    client = open_webui_api_client(
        "http://127.0.0.1:3000/",
        timeout_seconds=7.5,
        opener=opener,
    )

    session = client.authenticate_no_auth_admin()
    request = captured["request"]

    assert session["token"] == "session-token"
    assert request.full_url == "http://127.0.0.1:3000/api/v1/auths/signin"
    assert request.get_method() == "POST"
    assert json.loads(request.data.decode("utf-8")) == {
        "email": "admin@localhost",
        "password": "admin",
    }
    assert captured["timeout"] == 7.5


def test_get_readiness_uses_open_webui_ready_endpoint() -> None:
    captured = {}

    def opener(request, *, timeout):
        del timeout
        captured["request"] = request
        return fake_response({"status": True})

    client = open_webui_api_client("http://127.0.0.1:3000", opener=opener)

    result = client.get_readiness()

    assert result == {"status": True}
    assert captured["request"].full_url.endswith("/ready")


def test_update_ollama_config_uses_supported_admin_endpoint() -> None:
    captured = {}

    def opener(request, *, timeout):
        del timeout
        captured["request"] = request
        return fake_response(
            {
                "ENABLE_OLLAMA_API": True,
                "OLLAMA_BASE_URLS": ["http://host.docker.internal:11434"],
                "OLLAMA_API_CONFIGS": {},
            }
        )

    client = open_webui_api_client("http://127.0.0.1:3000", opener=opener)
    payload = {
        "ENABLE_OLLAMA_API": True,
        "OLLAMA_BASE_URLS": ["http://host.docker.internal:11434"],
        "OLLAMA_API_CONFIGS": {},
    }

    result = client.update_ollama_config("session-token", payload)
    request = captured["request"]

    assert result == payload
    assert request.full_url.endswith("/ollama/config/update")
    assert request.get_header("Authorization") == "Bearer session-token"
    assert json.loads(request.data.decode("utf-8")) == payload


def test_update_openai_config_uses_supported_admin_endpoint() -> None:
    captured = {}

    def opener(request, *, timeout):
        del timeout
        captured["request"] = request
        return fake_response(
            {
                "ENABLE_OPENAI_API": False,
                "OPENAI_API_BASE_URLS": [],
                "OPENAI_API_KEYS": [],
                "OPENAI_API_CONFIGS": {},
            }
        )

    client = open_webui_api_client("http://127.0.0.1:3000", opener=opener)
    payload = {
        "ENABLE_OPENAI_API": False,
        "OPENAI_API_BASE_URLS": [],
        "OPENAI_API_KEYS": [],
        "OPENAI_API_CONFIGS": {},
    }

    result = client.update_openai_config("session-token", payload)
    request = captured["request"]

    assert result == payload
    assert request.full_url.endswith("/openai/config/update")
    assert request.get_header("Authorization") == "Bearer session-token"
    assert json.loads(request.data.decode("utf-8")) == payload


def test_get_models_uses_bearer_token_and_extracts_data() -> None:
    captured = {}

    def opener(request, *, timeout):
        del timeout
        captured["request"] = request
        return fake_response(
            {
                "data": [
                    {"id": "nemotron-3-nano:30b-cloud"},
                ]
            }
        )

    client = open_webui_api_client("http://127.0.0.1:3000", opener=opener)

    models = client.get_models("session-token")
    request = captured["request"]

    assert models == [{"id": "nemotron-3-nano:30b-cloud"}]
    assert request.full_url.endswith("/api/models?refresh=true")
    assert request.get_header("Authorization") == "Bearer session-token"








def test_invalid_model_response_is_rejected() -> None:
    def opener(request, *, timeout):
        del request, timeout
        return fake_response({"data": {"id": "not-a-list"}})

    client = open_webui_api_client("http://127.0.0.1:3000", opener=opener)

    with pytest.raises(open_webui_api_error, match="model list"):
        client.get_models("session-token")


def test_config_export_and_import_use_admin_config_endpoints() -> None:
    requests = []

    def opener(request, *, timeout):
        del timeout
        requests.append(request)
        if request.get_method() == "GET":
            return fake_response({"task.title.enable": True})
        return fake_response({"task.title.enable": False})

    client = open_webui_api_client("http://127.0.0.1:3000", opener=opener)

    observed = client.get_config("session-token")
    updated = client.import_config(
        "session-token",
        {"task.title.enable": False},
    )

    assert observed == {"task.title.enable": True}
    assert updated == {"task.title.enable": False}
    assert requests[0].full_url.endswith("/api/v1/configs/export")
    assert requests[1].full_url.endswith("/api/v1/configs/import")
    assert json.loads(requests[1].data.decode("utf-8")) == {
        "config": {"task.title.enable": False}
    }
