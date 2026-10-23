from io import BytesIO
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest

from acmm_infra.agent_zero_api import agent_zero_api_client, agent_zero_api_error


_CSRF_TOKEN = "csrf-test-token"


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


def _is_csrf_request(request) -> bool:
    return urlsplit(request.full_url).path == "/api/csrf_token"


def _csrf_response() -> fake_response:
    return fake_response(
        {
            "ok": True,
            "token": _CSRF_TOKEN,
            "runtime_id": "test-runtime",
        }
    )


def _assert_csrf_header(request) -> None:
    assert request.get_header("X-csrf-token") == _CSRF_TOKEN
    assert request.get_header("Origin") == "http://127.0.0.1:50080"


def test_get_settings_uses_agent_zero_settings_endpoint() -> None:
    requests = []
    timeouts = []

    def opener(request, *, timeout):
        requests.append(request)
        timeouts.append(timeout)
        if _is_csrf_request(request):
            return _csrf_response()
        return fake_response({"settings": {"agent_profile": "acmm"}})

    client = agent_zero_api_client(
        "http://127.0.0.1:50080/",
        timeout_seconds=7.5,
        opener=opener,
    )

    result = client.get_settings()
    csrf_request, request = requests

    assert result == {"settings": {"agent_profile": "acmm"}}
    assert csrf_request.full_url == "http://127.0.0.1:50080/api/csrf_token"
    assert csrf_request.get_method() == "GET"
    assert csrf_request.get_header("Origin") == "http://127.0.0.1:50080"
    assert request.full_url == "http://127.0.0.1:50080/api/settings_get"
    assert request.get_method() == "POST"
    assert json.loads(request.data.decode("utf-8")) == {}
    _assert_csrf_header(request)
    assert timeouts == [7.5, 7.5]


def test_update_settings_posts_managed_delta() -> None:
    requests = []

    def opener(request, *, timeout):
        del timeout
        requests.append(request)
        if _is_csrf_request(request):
            return _csrf_response()
        return fake_response({"settings": {"agent_profile": "acmm"}})

    client = agent_zero_api_client("http://127.0.0.1:50080", opener=opener)

    result = client.update_settings({"agent_profile": "acmm"})
    request = requests[-1]

    assert result == {"settings": {"agent_profile": "acmm"}}
    assert request.full_url.endswith("/api/settings_set")
    assert request.get_method() == "POST"
    assert json.loads(request.data.decode("utf-8")) == {
        "settings": {"agent_profile": "acmm"}
    }
    _assert_csrf_header(request)


def test_get_model_config_scopes_request_to_agent_profile() -> None:
    requests = []

    def opener(request, *, timeout):
        del timeout
        requests.append(request)
        if _is_csrf_request(request):
            return _csrf_response()
        return fake_response(
            {
                "configured_preset": "Default",
                "selected_preset": "Default",
                "config": {},
            }
        )

    client = agent_zero_api_client("http://127.0.0.1:50080", opener=opener)

    client.get_model_config(agent_profile="acmm")
    request = requests[-1]

    assert request.full_url.endswith("/api/plugins/_model_config/model_config_get")
    assert json.loads(request.data.decode("utf-8")) == {"agent_profile": "acmm"}
    _assert_csrf_header(request)


def test_search_models_posts_provider_model_type_base_and_query() -> None:
    requests = []

    def opener(request, *, timeout):
        del timeout
        requests.append(request)
        if _is_csrf_request(request):
            return _csrf_response()
        return fake_response(
            {
                "models": ["nemotron-3-nano:30b-cloud"],
                "source": "provider_endpoint",
                "error": "",
            }
        )

    client = agent_zero_api_client("http://127.0.0.1:50080", opener=opener)

    result = client.search_models(
        provider="ollama",
        model_type="chat",
        api_base="http://host.docker.internal:11434",
        query="nemotron-3-nano:30b-cloud",
    )
    request = requests[-1]

    assert result["models"] == ["nemotron-3-nano:30b-cloud"]
    assert request.full_url.endswith("/api/plugins/_model_config/model_search")
    assert json.loads(request.data.decode("utf-8")) == {
        "provider": "ollama",
        "model_type": "chat",
        "api_base": "http://host.docker.internal:11434",
        "query": "nemotron-3-nano:30b-cloud",
    }
    _assert_csrf_header(request)


def test_http_error_includes_agent_zero_api_context() -> None:
    def opener(request, *, timeout):
        del timeout
        if _is_csrf_request(request):
            return _csrf_response()
        raise HTTPError(
            request.full_url,
            500,
            "Internal Server Error",
            {},
            BytesIO(b'{"error":"boom"}'),
        )

    client = agent_zero_api_client("http://127.0.0.1:50080", opener=opener)

    with pytest.raises(agent_zero_api_error, match="settings_get failed with HTTP 500"):
        client.get_settings()


def test_invalid_json_response_is_rejected() -> None:
    class invalid_json_response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            del exc_type, exc, traceback

        def read(self) -> bytes:
            return b"not-json"

    def opener(request, *, timeout):
        del timeout
        if _is_csrf_request(request):
            return _csrf_response()
        return invalid_json_response()

    client = agent_zero_api_client("http://127.0.0.1:50080", opener=opener)

    with pytest.raises(agent_zero_api_error, match="returned invalid JSON"):
        client.get_settings()


def test_model_presets_api_supports_get_save_and_profile_selection() -> None:
    requests = []

    def opener(request, *, timeout):
        del timeout
        requests.append(request)
        if _is_csrf_request(request):
            return _csrf_response()
        payload = json.loads(request.data.decode("utf-8"))
        action = payload["action"]
        if action == "get":
            return fake_response(
                {
                    "presets": [],
                    "configured_preset": "Default",
                    "selected_preset": "Default",
                }
            )
        if action == "save":
            return fake_response({"ok": True, "presets": payload["presets"]})
        if action == "select":
            return fake_response({"ok": True, "selected_preset": payload["name"]})
        raise AssertionError(action)

    client = agent_zero_api_client("http://127.0.0.1:50080", opener=opener)
    presets = [{"name": "Default", "chat": {}}]

    client.get_model_presets(agent_profile="acmm")
    client.save_model_presets(presets)
    client.select_model_preset(agent_profile="acmm", name="Default")

    api_requests = [request for request in requests if not _is_csrf_request(request)]
    assert len(requests) == 4
    assert all(
        request.full_url.endswith("/api/plugins/_model_config/model_presets")
        for request in api_requests
    )
    assert json.loads(api_requests[0].data.decode("utf-8")) == {
        "action": "get",
        "agent_profile": "acmm",
    }
    assert json.loads(api_requests[1].data.decode("utf-8")) == {
        "action": "save",
        "presets": presets,
    }
    assert json.loads(api_requests[2].data.decode("utf-8")) == {
        "action": "select",
        "agent_profile": "acmm",
        "name": "Default",
    }
    for request in api_requests:
        _assert_csrf_header(request)


def test_csrf_403_refreshes_token_and_retries_once() -> None:
    requests = []
    csrf_tokens = iter(["token-one", "token-two"])
    protected_attempt = 0

    def opener(request, *, timeout):
        nonlocal protected_attempt
        del timeout
        requests.append(request)
        if _is_csrf_request(request):
            return fake_response(
                {
                    "ok": True,
                    "token": next(csrf_tokens),
                    "runtime_id": "test-runtime",
                }
            )
        protected_attempt += 1
        if protected_attempt == 1:
            raise HTTPError(
                request.full_url,
                403,
                "Forbidden",
                {},
                BytesIO(b"CSRF token missing or invalid"),
            )
        return fake_response({"settings": {"agent_profile": "acmm"}})

    client = agent_zero_api_client("http://127.0.0.1:50080", opener=opener)

    result = client.get_settings()

    assert result == {"settings": {"agent_profile": "acmm"}}
    csrf_requests = [request for request in requests if _is_csrf_request(request)]
    api_requests = [request for request in requests if not _is_csrf_request(request)]
    assert len(csrf_requests) == 2
    assert len(api_requests) == 2
    assert api_requests[0].get_header("X-csrf-token") == "token-one"
    assert api_requests[1].get_header("X-csrf-token") == "token-two"


def test_csrf_initialization_error_is_reported() -> None:
    def opener(request, *, timeout):
        del request, timeout
        return fake_response(
            {
                "ok": False,
                "error": "Origin is not allowed",
            }
        )

    client = agent_zero_api_client("http://127.0.0.1:50080", opener=opener)

    with pytest.raises(agent_zero_api_error, match="Origin is not allowed"):
        client.get_settings()
