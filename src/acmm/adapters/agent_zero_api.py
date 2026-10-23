from __future__ import annotations

from http.cookiejar import CookieJar
import json
import socket
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


class agent_zero_runtime_api_error(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class agent_zero_runtime_api_client:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:50080",
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

    def get_external_api_token(self) -> str:
        result = self._protected_json("POST", "/api/settings_get", payload={})
        settings = result.get("settings")
        if not isinstance(settings, dict):
            raise agent_zero_runtime_api_error(
                "Agent Zero settings response did not contain a settings object."
            )

        token = settings.get("mcp_server_token")
        if not isinstance(token, str) or not token:
            raise agent_zero_runtime_api_error(
                "Agent Zero settings response did not contain an external API token."
            )
        return token

    def update_plugin_config(
        self,
        plugin_name: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        return self._protected_json(
            "POST",
            "/api/plugins",
            payload={
                "action": "save_config",
                "plugin_name": plugin_name,
                "project_name": "",
                "agent_profile": "",
                "settings": settings,
            },
        )

    def create_context(self, context_id: str) -> dict[str, Any]:
        return self._protected_json(
            "POST",
            "/api/chat_create",
            payload={
                "new_context": context_id,
                "project_name": "",
            },
        )

    def send_message(
        self,
        api_key: str,
        *,
        context_id: str,
        message: str,
        agent_profile: str,
        lifetime_hours: float,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        return self._external_json(
            "POST",
            "/api/api_message",
            api_key=api_key,
            payload={
                "context_id": context_id,
                "message": message,
                "attachments": [],
                "lifetime_hours": lifetime_hours,
                "project_name": "",
                "agent_profile": agent_profile,
            },
            timeout_seconds=timeout_seconds,
        )

    def get_logs(
        self,
        api_key: str,
        context_id: str,
        *,
        length: int = 1000,
    ) -> dict[str, Any]:
        return self._external_json(
            "POST",
            "/api/api_log_get",
            api_key=api_key,
            payload={
                "context_id": context_id,
                "length": length,
            },
        )

    def terminate_context(self, api_key: str, context_id: str) -> dict[str, Any]:
        return self._external_json(
            "POST",
            "/api/api_terminate_chat",
            api_key=api_key,
            payload={"context_id": context_id},
        )

    def _protected_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for retry in range(2):
            token = self._get_csrf_token()
            try:
                return self._send_json(
                    method,
                    path,
                    payload=payload,
                    headers={
                        "Origin": self.base_url,
                        "X-CSRF-Token": token,
                    },
                )
            except agent_zero_runtime_api_error as exc:
                if exc.status != 403 or retry != 0:
                    raise
                self._csrf_token = None
        raise AssertionError("unreachable")

    def _external_json(
        self,
        method: str,
        path: str,
        *,
        api_key: str,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self._send_json(
            method,
            path,
            payload=payload,
            headers={"X-API-KEY": api_key},
            timeout_seconds=timeout_seconds,
        )

    def _get_csrf_token(self) -> str:
        if self._csrf_token is not None:
            return self._csrf_token

        result = self._send_json(
            "GET",
            "/api/csrf_token",
            headers={"Origin": self.base_url},
        )
        if result.get("ok") is not True:
            detail = str(result.get("error") or "unknown CSRF initialization error")
            raise agent_zero_runtime_api_error(
                "Agent Zero API CSRF initialization failed: " + detail
            )

        token = result.get("token")
        if not isinstance(token, str) or not token:
            raise agent_zero_runtime_api_error(
                "Agent Zero API CSRF initialization returned no token."
            )
        self._csrf_token = token
        return token

    def _send_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)

        data = None
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")

        request = Request(
            self.base_url + path,
            data=data,
            headers=request_headers,
            method=method,
        )
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds

        try:
            with self._opener(request, timeout=timeout) as response:
                raw = response.read()
                status = int(getattr(response, "status", 200))
        except HTTPError as exc:
            detail = _http_error_detail(exc)
            raise agent_zero_runtime_api_error(
                f"Agent Zero API {method} {path} failed with HTTP {exc.code}: {detail}",
                status=exc.code,
            ) from exc
        except TimeoutError:
            raise
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError(
                    f"Agent Zero API {method} {path} exceeded its request timeout."
                ) from exc
            raise agent_zero_runtime_api_error(
                f"Agent Zero API {method} {path} could not connect: {exc.reason}"
            ) from exc
        except socket.timeout as exc:
            raise TimeoutError(
                f"Agent Zero API {method} {path} exceeded its request timeout."
            ) from exc
        except OSError as exc:
            raise agent_zero_runtime_api_error(
                f"Agent Zero API {method} {path} failed: {exc}"
            ) from exc

        if status < 200 or status >= 300:
            raise agent_zero_runtime_api_error(
                f"Agent Zero API {method} {path} returned HTTP {status}.",
                status=status,
            )
        if not raw:
            return {}

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise agent_zero_runtime_api_error(
                f"Agent Zero API {method} {path} returned invalid JSON."
            ) from exc
        if not isinstance(parsed, dict):
            raise agent_zero_runtime_api_error(
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
