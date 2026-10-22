from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from acmm_infra.common import infrastructure_error


class open_webui_api_error(infrastructure_error):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class open_webui_api_client:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._opener = opener or urlopen

    def authenticate_no_auth_admin(self) -> dict[str, Any]:
        session = self._request_json(
            "POST",
            "/api/v1/auths/signin",
            payload={
                "email": "admin@localhost",
                "password": "admin",
            },
        )
        token = session.get("token")
        if not isinstance(token, str) or not token:
            raise open_webui_api_error(
                "Open WebUI sign-in response did not contain a bearer token."
            )
        return session

    def get_readiness(self) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/ready",
        )

    def get_ollama_config(self, token: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/ollama/config",
            token=token,
        )

    def update_ollama_config(
        self,
        token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/ollama/config/update",
            payload=payload,
            token=token,
        )

    def get_openai_config(self, token: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/openai/config",
            token=token,
        )

    def update_openai_config(
        self,
        token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/openai/config/update",
            payload=payload,
            token=token,
        )

    def get_config(self, token: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/api/v1/configs/export",
            token=token,
        )

    def import_config(
        self,
        token: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/v1/configs/import",
            payload={"config": config},
            token=token,
        )

    def get_models(self, token: str) -> list[dict[str, Any]]:
        response = self._request_json(
            "GET",
            "/api/models?refresh=true",
            token=token,
        )
        models = response.get("data")
        if not isinstance(models, list):
            raise open_webui_api_error(
                "Open WebUI model response did not contain a model list."
            )
        return [item for item in models if isinstance(item, dict)]

    def get_tool(self, token: str, tool_id: str) -> dict[str, Any] | None:
        try:
            return self._request_json(
                "GET",
                f"/api/v1/tools/id/{tool_id}",
                token=token,
            )
        except open_webui_api_error as exc:
            if exc.status == 404:
                return None
            raise

    def create_tool(
        self,
        token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/v1/tools/create",
            payload=payload,
            token=token,
        )

    def update_tool(
        self,
        token: str,
        tool_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/api/v1/tools/id/{tool_id}/update",
            payload=payload,
            token=token,
        )

    def update_tool_valves(
        self,
        token: str,
        tool_id: str,
        valves: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/api/v1/tools/id/{tool_id}/valves/update",
            payload=valves,
            token=token,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
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

        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                status = getattr(response, "status", 200)
        except HTTPError as exc:
            detail = _http_error_detail(exc)
            raise open_webui_api_error(
                f"Open WebUI API {method} {path} failed with HTTP {exc.code}: {detail}",
                status=exc.code,
            ) from exc
        except URLError as exc:
            raise open_webui_api_error(
                f"Open WebUI API {method} {path} could not connect: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise open_webui_api_error(
                f"Open WebUI API {method} {path} failed: {exc}"
            ) from exc

        if status < 200 or status >= 300:
            raise open_webui_api_error(
                f"Open WebUI API {method} {path} returned HTTP {status}.",
                status=status,
            )

        if not raw:
            return {}

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise open_webui_api_error(
                f"Open WebUI API {method} {path} returned invalid JSON."
            ) from exc

        if not isinstance(parsed, dict):
            raise open_webui_api_error(
                f"Open WebUI API {method} {path} returned a non-object JSON response."
            )

        return parsed


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
