from __future__ import annotations

from http.cookiejar import CookieJar
import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from acmm_infra.common import infrastructure_error


class agent_zero_api_error(infrastructure_error):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class agent_zero_api_client:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        if opener is None:
            cookie_jar = CookieJar()
            self._opener = build_opener(HTTPCookieProcessor(cookie_jar)).open
        else:
            self._opener = opener
        self._csrf_token: str | None = None

    def get_settings(self) -> dict[str, Any]:
        return self._request_json("POST", "/api/settings_get", payload={})

    def update_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/settings_set",
            payload={"settings": settings},
        )

    def get_model_config(self, *, agent_profile: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/plugins/_model_config/model_config_get",
            payload={"agent_profile": agent_profile},
        )

    def get_model_presets(self, *, agent_profile: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/plugins/_model_config/model_presets",
            payload={"action": "get", "agent_profile": agent_profile},
        )

    def save_model_presets(self, presets: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/plugins/_model_config/model_presets",
            payload={"action": "save", "presets": presets},
        )

    def select_model_preset(
        self,
        *,
        agent_profile: str,
        name: str,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/plugins/_model_config/model_presets",
            payload={
                "action": "select",
                "agent_profile": agent_profile,
                "name": name,
            },
        )

    def search_models(
        self,
        *,
        provider: str,
        model_type: str,
        api_base: str,
        query: str = "",
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/plugins/_model_config/model_search",
            payload={
                "provider": provider,
                "model_type": model_type,
                "api_base": api_base,
                "query": query,
            },
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for retry in range(2):
            token = self._get_csrf_token()
            try:
                return self._send_json_request(
                    method,
                    path,
                    payload=payload,
                    csrf_token=token,
                )
            except agent_zero_api_error as exc:
                if exc.status != 403 or retry != 0:
                    raise
                self._csrf_token = None
        raise AssertionError("unreachable")

    def _get_csrf_token(self) -> str:
        if self._csrf_token is not None:
            return self._csrf_token

        request = Request(
            self.base_url + "/api/csrf_token",
            headers={
                "Accept": "application/json",
                "Origin": self.base_url,
            },
            method="GET",
        )
        response = self._send_request(request, "GET", "/api/csrf_token")
        if response.get("ok") is not True:
            detail = str(response.get("error") or "unknown CSRF initialization error")
            raise agent_zero_api_error(
                "Agent Zero API CSRF initialization failed: " + detail
            )

        token = response.get("token")
        if not isinstance(token, str) or not token:
            raise agent_zero_api_error(
                "Agent Zero API CSRF initialization returned no token."
            )
        self._csrf_token = token
        return token

    def _send_json_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None,
        csrf_token: str,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Origin": self.base_url,
            "X-CSRF-Token": csrf_token,
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")

        request = Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        return self._send_request(request, method, path)

    def _send_request(
        self,
        request: Request,
        method: str,
        path: str,
    ) -> dict[str, Any]:
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                status = int(getattr(response, "status", 200))
        except HTTPError as exc:
            detail = _http_error_detail(exc)
            raise agent_zero_api_error(
                f"Agent Zero API {method} {path} failed with HTTP {exc.code}: {detail}",
                status=exc.code,
            ) from exc
        except URLError as exc:
            raise agent_zero_api_error(
                f"Agent Zero API {method} {path} could not connect: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise agent_zero_api_error(
                f"Agent Zero API {method} {path} failed: {exc}"
            ) from exc

        if status < 200 or status >= 300:
            raise agent_zero_api_error(
                f"Agent Zero API {method} {path} returned HTTP {status}.",
                status=status,
            )
        if not raw:
            return {}

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise agent_zero_api_error(
                f"Agent Zero API {method} {path} returned invalid JSON."
            ) from exc
        if not isinstance(parsed, dict):
            raise agent_zero_api_error(
                f"Agent Zero API {method} {path} returned a non-object JSON response."
            )
        return parsed


def _http_error_detail(exc: HTTPError) -> str:
    try:
        raw = exc.read()
    except OSError:
        return str(exc.reason or "request failed")
    if not raw:
        return str(exc.reason or "request failed")
    return raw.decode("utf-8", errors="replace").strip() or "request failed"
