from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class open_webui_runtime_api_error(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class open_webui_runtime_api_client:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:3000",
        *,
        timeout_seconds: float = 10.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._opener = opener or urlopen

    def authenticate_no_auth_admin(self) -> dict[str, Any]:
        result = self._request_json(
            "POST",
            "/api/v1/auths/signin",
            payload={
                "email": "admin@localhost",
                "password": "admin",
            },
        )
        return _require_object(result, "Open WebUI sign-in response")


    def create_chat(
        self,
        token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = self._request_json(
            "POST",
            "/api/v1/chats/new",
            payload=payload,
            token=token,
        )
        return _require_object(result, "Open WebUI chat creation response")

    def complete_chat(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> Any:
        return self._request_json(
            "POST",
            "/api/chat/completions",
            payload=payload,
            token=token,
            timeout_seconds=timeout_seconds,
        )

    def get_chat(self, token: str, chat_id: str) -> dict[str, Any]:
        result = self._request_json(
            "GET",
            f"/api/v1/chats/{chat_id}",
            token=token,
        )
        return _require_object(result, "Open WebUI chat response")

    def delete_chat(self, token: str, chat_id: str) -> Any:
        return self._request_json(
            "DELETE",
            f"/api/v1/chats/{chat_id}",
            token=token,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
        timeout_seconds: float | None = None,
    ) -> Any:
        headers = {
            "Accept": "application/json",
        }
        data = None

        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")

        if token is not None:
            headers["Authorization"] = f"Bearer {token}"

        request = Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds

        try:
            with self._opener(request, timeout=timeout) as response:
                raw = response.read()
                status = getattr(response, "status", 200)
        except HTTPError as exc:
            detail = _http_error_detail(exc)
            raise open_webui_runtime_api_error(
                f"Open WebUI API {method} {path} failed with HTTP {exc.code}: {detail}",
                status=exc.code,
            ) from exc
        except URLError as exc:
            raise open_webui_runtime_api_error(
                f"Open WebUI API {method} {path} could not connect: {exc.reason}"
            ) from exc
        except TimeoutError:
            raise
        except OSError as exc:
            raise open_webui_runtime_api_error(
                f"Open WebUI API {method} {path} failed: {exc}"
            ) from exc

        if status < 200 or status >= 300:
            raise open_webui_runtime_api_error(
                f"Open WebUI API {method} {path} returned HTTP {status}.",
                status=status,
            )

        if not raw:
            return None

        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise open_webui_runtime_api_error(
                f"Open WebUI API {method} {path} returned invalid JSON."
            ) from exc


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise open_webui_runtime_api_error(f"{label} is not a JSON object.")
    return value


def _http_error_detail(exc: HTTPError) -> str:
    try:
        raw = exc.read()
    except OSError:
        return exc.reason or "request failed"

    if not raw:
        return exc.reason or "request failed"

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace").strip() or "request failed"

    if isinstance(parsed, dict):
        detail = parsed.get("detail")
        if isinstance(detail, str) and detail:
            return detail

    return str(parsed)
