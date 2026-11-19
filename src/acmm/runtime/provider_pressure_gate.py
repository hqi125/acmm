from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from types import MappingProxyType
from typing import Any, Mapping

from acmm.runtime.provider_pressure import (
    provider_pressure_engine,
    provider_pressure_execution_session,
    runtime_provider_pressure_action_result_event,
    runtime_provider_pressure_gate_configuration,
    runtime_provider_pressure_recovery_event,
    runtime_provider_pressure_run_context,
    runtime_provider_pressure_transport_action,
    runtime_provider_pressure_transport_recovery,
    runtime_provider_pressure_transport_request,
    runtime_provider_pressure_transport_response,
)


_CONTROL_SCHEMA_VERSION = "0.2"
_GATE_SCHEMA_VERSION = "0.1"
_MAX_BODY_BYTES = 256 * 1024


@dataclass
class _active_run:
    context: runtime_provider_pressure_run_context


@dataclass
class _admission:
    context: runtime_provider_pressure_run_context
    request: runtime_provider_pressure_transport_request
    action: runtime_provider_pressure_transport_action
    decision_id: str
    features: Mapping[str, float]
    admit_fingerprint: str
    admit_response: Mapping[str, Any]
    feedback_responses: dict[int, tuple[str, Mapping[str, Any]]]
    cancel_fingerprint: str | None = None
    cancel_response: Mapping[str, Any] | None = None
    terminal: bool = False


class provider_pressure_transport_gate:
    """Authenticated synchronous bridge from external provider transports into PPH.

    The bridge is provider-neutral.  The package model classifies transport requests
    and provider responses; Core owns admission, recovery actuation and result
    correlation.  request_id and (request_id, attempt) are idempotency keys for
    admission/cancellation and feedback delivery, respectively, so control-plane response loss can
    be retried without duplicating provider-pressure state transitions.  The control
    file is deliberately separate from the HTTP endpoint so
    long-lived transports can discover whether an execution currently owns the gate.
    """

    def __init__(
        self,
        engine: provider_pressure_engine,
        session: provider_pressure_execution_session,
        configuration: runtime_provider_pressure_gate_configuration,
    ) -> None:
        self._engine = engine
        self._session = session
        self._configuration = configuration
        self._token = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self._active: _active_run | None = None
        self._admissions: dict[str, _admission] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def configuration(self) -> runtime_provider_pressure_gate_configuration:
        return self._configuration

    def start(self) -> None:
        if self._server is not None:
            return
        gate = self

        class _handler(BaseHTTPRequestHandler):
            server_version = "ACMMProviderPressureGate/0.2"
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                return

            def do_POST(self) -> None:  # noqa: N802
                gate._handle(self)

        self._server = ThreadingHTTPServer(
            (self._configuration.bind_host, self._configuration.bind_port),
            _handler,
        )
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="acmm-provider-pressure-gate",
            daemon=True,
        )
        self._thread.start()
        self._write_control(active=False, run_context=None)

    def close(self) -> None:
        quiesce_error: Exception | None = None
        with self._lock:
            try:
                self._quiesce_locked("provider pressure gate closed")
            except Exception as exc:
                quiesce_error = exc
            finally:
                self._active = None
                self._admissions.clear()
                self._write_control(active=False, run_context=None)
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        try:
            Path(self._configuration.control_path).unlink(missing_ok=True)
        except OSError:
            pass
        if quiesce_error is not None:
            raise quiesce_error

    def activate(self, context: runtime_provider_pressure_run_context) -> None:
        with self._lock:
            self._active = _active_run(context=context)
            self._admissions.clear()
            self._write_control(active=True, run_context=context)

    def deactivate(self) -> None:
        quiesce_error: Exception | None = None
        with self._lock:
            try:
                self._quiesce_locked("provider pressure gate deactivated")
            except Exception as exc:
                quiesce_error = exc
            finally:
                self._active = None
                self._admissions.clear()
                self._write_control(active=False, run_context=None)
        if quiesce_error is not None:
            raise quiesce_error

    def quiesce(self, detail: str = "provider pressure gate quiesced") -> int:
        """Terminalize all in-flight transport admissions without deactivating.

        Interruption cleanup may still need trusted provider traffic.  Quiescing
        records any request whose control exchange was abandoned as interrupted,
        while keeping the active run context available for subsequent governed
        cleanup requests.
        """

        with self._lock:
            return self._quiesce_locked(detail)

    def _quiesce_locked(self, detail: str) -> int:
        terminalized = 0
        errors: list[str] = []
        for request_id, admission in self._admissions.items():
            if admission.terminal:
                continue
            try:
                self._engine.action_result_observe(
                    self._session,
                    admission.context,
                    runtime_provider_pressure_action_result_event(
                        outcome="interrupted",
                        action_id=admission.action.action_id,
                        decision_id=admission.decision_id,
                        features=admission.features,
                        metadata={
                            "transport_plane": admission.request.plane,
                            "transport_request_kind": admission.request.request_kind,
                            "transport_url": admission.request.url,
                            "transport_method": admission.request.method,
                            "detail": str(detail or "transport quiesced"),
                            "control_request_id": request_id,
                        },
                    ),
                )
            except Exception as exc:
                errors.append(
                    f"{request_id}: {str(exc) or exc.__class__.__name__}"
                )
                continue
            admission.terminal = True
            terminalized += 1
        if errors:
            raise RuntimeError(
                "Could not quiesce all Provider Pressure gate admissions: "
                + "; ".join(errors)
            )
        return terminalized

    def _write_control(
        self,
        *,
        active: bool,
        run_context: runtime_provider_pressure_run_context | None,
    ) -> None:
        path = Path(self._configuration.control_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        value: dict[str, Any] = {
            "schema_version": _CONTROL_SCHEMA_VERSION,
            "active": bool(active),
            "url": self._configuration.advertised_url.rstrip("/"),
            "local_url": f"http://127.0.0.1:{self._configuration.bind_port}",
            "token": self._token if active else "",
            "provider_id": self._session.identity.provider_id,
            "execution_id": self._session.context.execution_id,
        }
        if run_context is not None:
            value["run"] = {
                "run_index": run_context.run_index,
                "subject_id": run_context.subject_id,
                "scenario_id": run_context.scenario_id,
                "repetition": run_context.repetition,
                "materialization_key": run_context.materialization_key,
            }
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(path)
        path.chmod(0o600)

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.headers.get("Authorization", "") != "Bearer " + self._token:
            handler.close_connection = True
            self._json(handler, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        length = _safe_int(handler.headers.get("Content-Length"))
        if length < 0 or length > _MAX_BODY_BYTES:
            handler.close_connection = True
            self._json(handler, HTTPStatus.BAD_REQUEST, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(handler.rfile.read(length) if length else b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(handler, HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return
        if not isinstance(payload, Mapping):
            self._json(handler, HTTPStatus.BAD_REQUEST, {"error": "invalid payload"})
            return
        try:
            if handler.path == "/v1/admit":
                result = self._admit(payload)
            elif handler.path == "/v1/feedback":
                result = self._feedback(payload)
            elif handler.path == "/v1/cancel":
                result = self._cancel(payload)
            else:
                self._json(handler, HTTPStatus.NOT_FOUND, {"error": "unknown endpoint"})
                return
        except _gate_unavailable as exc:
            self._json(handler, HTTPStatus.CONFLICT, {"error": str(exc)})
            return
        except Exception as exc:
            self._json(
                handler,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc) or exc.__class__.__name__},
            )
            return
        self._json(handler, HTTPStatus.OK, result)

    def _admit(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request_id = _required_text(payload, "request_id")
        fingerprint = _payload_fingerprint(payload)
        request = runtime_provider_pressure_transport_request(
            plane=_required_text(payload, "plane"),
            method=_required_text(payload, "method").upper(),
            url=_required_text(payload, "url"),
            request_kind=str(payload.get("request_kind") or ""),
            metadata=_mapping(payload.get("metadata")),
        )
        with self._lock:
            active = self._active
            if active is None:
                raise _gate_unavailable("provider pressure gate has no active run")
            existing = self._admissions.get(request_id)
            if existing is not None:
                if existing.admit_fingerprint != fingerprint:
                    raise _gate_unavailable(
                        "provider pressure request id was reused with a different admission payload"
                    )
                return dict(existing.admit_response)
            classify = getattr(self._session.model, "transport_action", None)
            if not callable(classify):
                raise _gate_unavailable(
                    "provider pressure model does not classify transport actions"
                )
            action = classify(active.context, request)
            if not isinstance(action, runtime_provider_pressure_transport_action):
                raise TypeError(
                    "transport_action() must return runtime_provider_pressure_transport_action"
                )
            admitted = self._engine.action_admit(
                self._session,
                active.context,
                action_id=action.action_id,
                workload=action.workload,
            )
            decision_id = str(admitted.record.get("decision_id") or "")
            if not decision_id:
                raise RuntimeError("Provider Pressure admission did not produce a decision id.")
            response = {
                "schema_version": _GATE_SCHEMA_VERSION,
                "request_id": request_id,
                "action_id": action.action_id,
                "decision_id": decision_id,
                "admission": dict(admitted.record),
            }
            self._admissions[request_id] = _admission(
                context=active.context,
                request=request,
                action=action,
                decision_id=decision_id,
                features=MappingProxyType(dict(action.workload.features)),
                admit_fingerprint=fingerprint,
                admit_response=MappingProxyType(dict(response)),
                feedback_responses={},
            )
            return response

    def _feedback(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request_id = _required_text(payload, "request_id")
        attempt = _feedback_attempt(payload)
        fingerprint = _payload_fingerprint(payload)
        with self._lock:
            admission = self._admissions.get(request_id)
            if admission is None:
                raise _gate_unavailable("unknown provider pressure request id")
            cached = admission.feedback_responses.get(attempt)
            if cached is not None:
                cached_fingerprint, cached_response = cached
                if cached_fingerprint != fingerprint:
                    raise _gate_unavailable(
                        "provider pressure feedback attempt was reused with a different payload"
                    )
                return dict(cached_response)
            if admission.terminal:
                raise _gate_unavailable("provider pressure request is already terminal")
            response = runtime_provider_pressure_transport_response(
                status=_optional_status(payload.get("status")),
                headers={str(k): str(v) for k, v in _mapping(payload.get("headers")).items()},
                body=str(payload.get("body") or ""),
                error=(str(payload.get("error")) if payload.get("error") is not None else None),
                metadata=_mapping(payload.get("metadata")),
            )
            classify = getattr(self._session.model, "transport_recovery", None)
            recovery: runtime_provider_pressure_transport_recovery | None = None
            if callable(classify):
                recovery = classify(admission.context, admission.request, response)
                if recovery is not None and not isinstance(
                    recovery, runtime_provider_pressure_transport_recovery
                ):
                    raise TypeError(
                        "transport_recovery() must return "
                        "runtime_provider_pressure_transport_recovery or None"
                    )
            if recovery is not None:
                admitted = self._engine.recovery_actuate(
                    self._session,
                    admission.context,
                    runtime_provider_pressure_recovery_event(
                        kind=recovery.kind,
                        wait_seconds=float(recovery.wait_seconds),
                        recommendation_source=recovery.recommendation_source,
                        action_id=admission.action.action_id,
                        decision_id=admission.decision_id,
                        features=admission.features,
                        metadata={
                            "transport_plane": admission.request.plane,
                            "transport_request_kind": admission.request.request_kind,
                            "transport_url": admission.request.url,
                            "transport_method": admission.request.method,
                            "provider_status": response.status,
                            **dict(admission.action.metadata),
                            **dict(recovery.metadata),
                        },
                    ),
                )
                result = {
                    "schema_version": _GATE_SCHEMA_VERSION,
                    "request_id": request_id,
                    "action_id": admission.action.action_id,
                    "decision_id": admission.decision_id,
                    "retry": bool(recovery.retry),
                    "recovery": dict(admitted.record),
                }
                admission.feedback_responses[attempt] = (
                    fingerprint,
                    MappingProxyType(dict(result)),
                )
                if not recovery.retry:
                    admission.terminal = True
                return result

            outcome = (
                "failure"
                if response.error or (response.status is not None and response.status >= 400)
                else "success"
            )
            observed = self._engine.action_result_observe(
                self._session,
                admission.context,
                runtime_provider_pressure_action_result_event(
                    outcome=outcome,
                    action_id=admission.action.action_id,
                    decision_id=admission.decision_id,
                    features=admission.features,
                    metadata={
                        "transport_plane": admission.request.plane,
                        "transport_request_kind": admission.request.request_kind,
                        "transport_url": admission.request.url,
                        "transport_method": admission.request.method,
                        "provider_status": response.status,
                        "provider_error": response.error,
                        **dict(admission.action.metadata),
                    },
                ),
            )
            result = {
                "schema_version": _GATE_SCHEMA_VERSION,
                "request_id": request_id,
                "action_id": admission.action.action_id,
                "decision_id": admission.decision_id,
                "retry": False,
                "result": dict(observed),
            }
            admission.feedback_responses[attempt] = (
                fingerprint,
                MappingProxyType(dict(result)),
            )
            admission.terminal = True
            return result

    def _cancel(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request_id = _required_text(payload, "request_id")
        fingerprint = _payload_fingerprint(payload)
        with self._lock:
            admission = self._admissions.get(request_id)
            if admission is None:
                raise _gate_unavailable("unknown provider pressure request id")
            if admission.cancel_response is not None:
                if admission.cancel_fingerprint != fingerprint:
                    raise _gate_unavailable(
                        "provider pressure cancellation was reused with a different payload"
                    )
                return dict(admission.cancel_response)
            if admission.terminal:
                raise _gate_unavailable("provider pressure request is already terminal")
            observed = self._engine.action_result_observe(
                self._session,
                admission.context,
                runtime_provider_pressure_action_result_event(
                    outcome="interrupted",
                    action_id=admission.action.action_id,
                    decision_id=admission.decision_id,
                    features=admission.features,
                    metadata={
                        "transport_plane": admission.request.plane,
                        "transport_url": admission.request.url,
                        "transport_method": admission.request.method,
                        "detail": str(payload.get("detail") or "transport cancelled"),
                    },
                ),
            )
            result = {
                "schema_version": _GATE_SCHEMA_VERSION,
                "request_id": request_id,
                "result": dict(observed),
            }
            admission.cancel_fingerprint = fingerprint
            admission.cancel_response = MappingProxyType(dict(result))
            admission.terminal = True
            return result

    @staticmethod
    def _json(
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        value: Mapping[str, Any],
    ) -> None:
        data = (json.dumps(dict(value), sort_keys=True) + "\n").encode("utf-8")
        try:
            handler.send_response(int(status))
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        except OSError:
            # During Ctrl+C or client-side timeout recovery the caller can leave
            # after Core has already completed the idempotent control operation.
            # The state transition is authoritative; a dead response socket is
            # not a gate-server failure and should not emit a thread traceback.
            handler.close_connection = True


class _gate_unavailable(RuntimeError):
    pass


def _required_text(value: Mapping[str, Any], key: str) -> str:
    rendered = str(value.get(key) or "").strip()
    if not rendered:
        raise ValueError(f"Provider Pressure gate field '{key}' is required.")
    return rendered


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _payload_fingerprint(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _feedback_attempt(value: Mapping[str, Any]) -> int:
    raw = _mapping(value.get("metadata")).get("attempt", 0)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("Provider Pressure gate feedback attempt is invalid.")
    attempt = int(raw)
    if float(raw) != float(attempt) or attempt < 0:
        raise ValueError("Provider Pressure gate feedback attempt is invalid.")
    return attempt


def _optional_status(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Provider Pressure gate response status is invalid.")
    return int(value)


def _safe_int(value: str | None) -> int:
    try:
        return int(value or "0")
    except ValueError:
        return -1


__all__ = ["provider_pressure_transport_gate"]
