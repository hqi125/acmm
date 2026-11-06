from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Protocol
from uuid import uuid4

from acmm.evidence.structured import decode_json_object_text
from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    execution_failure,
)


class agent_zero_adapter_error(RuntimeError):
    pass


class agent_zero_adapter_client(Protocol):
    def get_external_api_token(self) -> str:
        ...

    def get_model_config(self, *, agent_profile: str) -> dict[str, Any]:
        ...


    def create_context(self, context_id: str) -> dict[str, Any]:
        ...

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
        ...

    def get_logs(
        self,
        api_key: str,
        context_id: str,
        *,
        length: int = 1000,
    ) -> dict[str, Any]:
        ...

    def terminate_context(self, api_key: str, context_id: str) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class agent_zero_adapter_config:
    agent_profile: str = "acmm"
    context_lifetime_hours: float = 1.0
    log_length: int = 1000
    terminate_context_after_run: bool = True


class agent_zero_adapter:
    def __init__(
        self,
        client: agent_zero_adapter_client,
        config: agent_zero_adapter_config | None = None,
    ) -> None:
        self._client = client
        self._config = config or agent_zero_adapter_config()

    def execute(
        self,
        subject: dict[str, Any],
        scenario: dict[str, Any],
        environment: Any,
        termination: dict[str, Any],
    ) -> adapter_execution_result:
        api_key: str | None = None
        context_id: str | None = None
        prompt: str | None = None
        model_observation: dict[str, Any] | None = None

        try:
            api_key = self._client.get_external_api_token()

            prompt = _required_string(
                scenario.get("task", {}).get("prompt"),
                "scenario task prompt",
            )
            context_id = str(uuid4())
            created = self._client.create_context(context_id)
            created_id = _required_string(
                created.get("ctxid"),
                "created context id",
            )
            if created_id != context_id:
                raise agent_zero_adapter_error(
                    "Agent Zero created a different context ID than requested."
                )

            model_observation = self._client.get_model_config(
                agent_profile=self._config.agent_profile
            )
            model_failure = _validate_model_observation(subject, model_observation)
            if model_failure is not None:
                return adapter_execution_result(
                    termination="harness_error",
                    evidence=(),
                    subject_provenance=_subject_provenance(
                        subject=subject,
                        context_id=context_id,
                        agent_profile=self._config.agent_profile,
                        model_observation=model_observation,
                    ),
                    failure=model_failure,
                )

            response = self._client.send_message(
                api_key,
                context_id=context_id,
                message=prompt,
                agent_profile=self._config.agent_profile,
                lifetime_hours=self._config.context_lifetime_hours,
                timeout_seconds=_adapter_timeout(termination),
            )
            returned_id = _required_string(
                response.get("context_id"),
                "message response context id",
            )
            if returned_id != context_id:
                raise agent_zero_adapter_error(
                    "Agent Zero message response referenced a different context ID."
                )

            response_text = response.get("response")
            if not isinstance(response_text, str):
                raise agent_zero_adapter_error(
                    "Agent Zero final response is not text."
                )
            final_response = response_text.strip()
            logs = self._client.get_logs(
                api_key,
                context_id,
                length=self._config.log_length,
            )
            parsed = _parse_run(
                logs=logs,
                context_id=context_id,
                prompt=prompt,
                final_response=final_response,
                subject=subject,
                agent_profile=self._config.agent_profile,
                model_observation=model_observation,
            )

            if parsed.failure is not None:
                return adapter_execution_result(
                    termination="harness_error",
                    evidence=parsed.evidence,
                    subject_provenance=parsed.subject_provenance,
                    failure=parsed.failure,
                )

            return adapter_execution_result(
                termination="agent_completed",
                evidence=parsed.evidence,
                subject_provenance=parsed.subject_provenance,
            )

        except TimeoutError as exc:
            if api_key is None or context_id is None or prompt is None:
                return _failure_result(
                    "adapter_process_timeout",
                    str(exc) or "Agent Zero execution exceeded the run timeout.",
                )

            try:
                logs = self._client.get_logs(
                    api_key,
                    context_id,
                    length=self._config.log_length,
                )
                parsed = _parse_run(
                    logs=logs,
                    context_id=context_id,
                    prompt=prompt,
                    final_response="",
                    subject=subject,
                        agent_profile=self._config.agent_profile,
                    model_observation=model_observation,
                    require_final_response=False,
                )
            except Exception as log_exc:
                return _failure_result(
                    "timeout_evidence_capture_failed",
                    str(log_exc) or log_exc.__class__.__name__,
                )

            if parsed.failure is not None:
                return adapter_execution_result(
                    termination="harness_error",
                    evidence=parsed.evidence,
                    subject_provenance=parsed.subject_provenance,
                    failure=parsed.failure,
                )

            return adapter_execution_result(
                termination="timeout",
                evidence=parsed.evidence,
                subject_provenance=parsed.subject_provenance,
            )
        except Exception as exc:
            return _failure_result(
                "adapter_process_error",
                str(exc) or exc.__class__.__name__,
            )
        finally:
            if (
                self._config.terminate_context_after_run
                and api_key is not None
                and context_id is not None
            ):
                try:
                    self._client.terminate_context(api_key, context_id)
                except Exception:
                    pass



@dataclass(frozen=True)
class _parsed_run:
    evidence: tuple[captured_evidence, ...]
    subject_provenance: dict[str, Any] | None
    failure: execution_failure | None


def _parse_run(
    *,
    logs: dict[str, Any],
    context_id: str,
    prompt: str,
    final_response: str,
    subject: dict[str, Any],
    agent_profile: str,
    model_observation: dict[str, Any] | None,
    require_final_response: bool = True,
) -> _parsed_run:
    returned_context_id = logs.get("context_id")
    if returned_context_id != context_id:
        return _parse_failure(
            "context_mismatch",
            "Agent Zero log response referenced a different context ID.",
        )

    log = logs.get("log")
    if not isinstance(log, dict):
        return _parse_failure(
            "missing_agent_log",
            "Agent Zero log response did not contain a log object.",
        )

    items = log.get("items")
    if not isinstance(items, list):
        return _parse_failure(
            "missing_agent_log_items",
            "Agent Zero log response did not contain log items.",
        )

    conversation, tool_calls, tool_results = _normalize_log_items(
        prompt=prompt,
        items=items,
        final_response=final_response,
    )
    provenance = _subject_provenance(
        subject=subject,
        context_id=context_id,
        agent_profile=agent_profile,
        model_observation=model_observation,
    )
    evidence = (
        captured_evidence(
            id="conversation",
            channel="conversation",
            artifact="evidence/conversation.json",
            content=_json_bytes(
                {
                    "context_id": context_id,
                    "messages": conversation,
                }
            ),
        ),
        captured_evidence(
            id="tool_calls",
            channel="tool_calls",
            artifact="evidence/tool_calls.json",
            content=_json_bytes(tool_calls),
        ),
        captured_evidence(
            id="tool_results",
            channel="tool_results",
            artifact="evidence/tool_results.json",
            content=_json_bytes(tool_results),
        ),
        captured_evidence(
            id="final_response",
            channel="final_response",
            artifact="evidence/final_response.txt",
            content=final_response.encode("utf-8"),
        ),
    )

    if require_final_response and not final_response:
        return _parsed_run(
            evidence=evidence,
            subject_provenance=provenance,
            failure=execution_failure(
                kind="missing_final_response",
                message="Agent Zero completed the run without final response text.",
            ),
        )

    return _parsed_run(
        evidence=evidence,
        subject_provenance=provenance,
        failure=None,
    )


def _normalize_log_items(
    *,
    prompt: str,
    items: list[Any],
    final_response: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    conversation: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": prompt,
        }
    ]
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    pending_request: dict[str, Any] | None = None

    def append_call(
        *,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        timestamp: Any,
    ) -> None:
        tool_calls.append(
            {
                "id": call_id,
                "name": name,
                "input": arguments,
                "timestamp": timestamp,
            }
        )
        conversation.append(
            {
                "role": "assistant",
                "content": "",
                "timestamp": timestamp,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(
                                arguments,
                                ensure_ascii=True,
                                separators=(",", ":"),
                            ),
                        },
                    }
                ],
            }
        )

    def append_result(
        *,
        call_id: str,
        name: str,
        content: str,
        timestamp: Any,
    ) -> None:
        is_error = _tool_output_has_error(content)
        tool_results.append(
            {
                "id": call_id,
                "name": name,
                "output": content,
                "is_error": is_error,
                "timestamp": timestamp,
            }
        )
        conversation.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": content,
                "is_error": is_error,
                "status": "failed" if is_error else "completed",
                "timestamp": timestamp,
            }
        )

    def flush_pending_call() -> None:
        nonlocal pending_request
        if pending_request is None:
            return
        append_call(
            call_id=pending_request["id"],
            name=pending_request["name"],
            arguments=pending_request["arguments"],
            timestamp=pending_request["timestamp"],
        )
        pending_request = None

    for item in items:
        if not isinstance(item, dict):
            continue

        kvps = item.get("kvps")
        if not isinstance(kvps, dict):
            kvps = {}

        name = _native_tool_log_name(item, kvps)
        if name is None or name == "response":
            continue

        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"agent-zero-tool-{item.get('no', len(tool_calls))}"

        arguments = _native_tool_log_arguments(kvps)
        content = item.get("content")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        timestamp = item.get("timestamp")

        embedded_request = _embedded_native_tool_request(content)
        if embedded_request is not None:
            embedded_name = embedded_request["tool_name"]
            if embedded_name == name:
                flush_pending_call()
                pending_request = {
                    "id": call_id,
                    "name": name,
                    "arguments": embedded_request["tool_args"],
                    "timestamp": timestamp,
                }
                continue

        if pending_request is not None:
            if pending_request["name"] == name:
                pending = pending_request
                pending_request = None
                append_call(
                    call_id=pending["id"],
                    name=pending["name"],
                    arguments=pending["arguments"],
                    timestamp=pending["timestamp"],
                )
                append_result(
                    call_id=pending["id"],
                    name=name,
                    content=content,
                    timestamp=timestamp,
                )
                continue
            flush_pending_call()

        append_call(
            call_id=call_id,
            name=name,
            arguments=arguments,
            timestamp=timestamp,
        )
        append_result(
            call_id=call_id,
            name=name,
            content=content,
            timestamp=timestamp,
        )

    flush_pending_call()
    conversation.append(
        {
            "role": "assistant",
            "content": final_response,
        }
    )
    return conversation, tool_calls, tool_results


def _embedded_native_tool_request(content: str) -> dict[str, Any] | None:
    decoded = decode_json_object_text(
        content,
        required_keys=("tool_name", "tool_args"),
    )
    if decoded is None:
        return None
    tool_name = decoded.value.get("tool_name")
    tool_args = decoded.value.get("tool_args")
    if (
        not isinstance(tool_name, str)
        or not tool_name.strip()
        or not isinstance(tool_args, dict)
    ):
        return None
    return {
        "tool_name": tool_name.strip(),
        "tool_args": {str(key): value for key, value in tool_args.items()},
    }

def _native_tool_log_name(
    item: dict[str, Any],
    kvps: dict[str, Any],
) -> str | None:
    for source in (kvps, item):
        for key in ("_tool_name", "tool_name", "qualified_name"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    server = kvps.get("server") or kvps.get("server_id")
    tool = kvps.get("tool") or kvps.get("name")
    item_type = str(item.get("type") or "").lower()
    if (
        "mcp" in item_type
        and isinstance(server, str)
        and server.strip()
        and isinstance(tool, str)
        and tool.strip()
    ):
        return f"mcp:{server.strip()}:{tool.strip()}"

    heading = item.get("heading")
    if not isinstance(heading, str):
        return None

    for pattern in (
        r"\b(mcp:[A-Za-z0-9_-]+:[A-Za-z0-9_.-]+)\b",
        r"\b(mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+)\b",
        r"\bUsing(?:\s+MCP)?\s+tool\s+['\"]([^'\"]+)['\"]",
    ):
        match = re.search(pattern, heading, flags=re.IGNORECASE)
        if match is not None:
            return match.group(1)

    return None


def _native_tool_log_arguments(kvps: dict[str, Any]) -> dict[str, Any]:
    for key in ("tool_args", "arguments", "args", "input"):
        value = kvps.get(key)
        if isinstance(value, dict):
            return {str(name): item for name, item in value.items()}

    metadata = {
        "tool_name",
        "qualified_name",
        "server",
        "server_id",
        "tool",
        "name",
        "result",
        "output",
    }
    return {
        str(key): value
        for key, value in kvps.items()
        if isinstance(key, str)
        and not key.startswith("_")
        and key not in metadata
    }


def _json_object_configuration(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise agent_zero_adapter_error(f"{name} must be a JSON object string.")

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise agent_zero_adapter_error(
            f"{name} must contain valid JSON."
        ) from exc

    if not isinstance(parsed, dict):
        raise agent_zero_adapter_error(f"{name} must contain a JSON object.")

    return parsed


def _adapter_timeout(termination: dict[str, Any]) -> float:
    timeout = termination.get("timeout_seconds")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise agent_zero_adapter_error(
            "termination.timeout_seconds must be a positive number."
        )
    return float(timeout)


def _tool_output_has_error(output: str) -> bool:
    value: Any = output
    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            break

    if not isinstance(value, dict):
        return False
    return value.get("ok") is False or "error" in value


def _validate_model_observation(
    subject: dict[str, Any],
    response: dict[str, Any],
) -> execution_failure | None:
    observed = _model_observation_fields(response)
    expected_model = _required_string(
        subject.get("model", {}).get("name"),
        "subject model name",
    )
    actual_model = observed.get("reported_model")
    if actual_model != expected_model:
        return execution_failure(
            kind="subject_model_mismatch",
            message=(
                f"Agent Zero model configuration reported {actual_model!r}; "
                f"Contract subject requires {expected_model!r}."
            ),
        )

    configuration = subject.get("configuration", {})
    if not isinstance(configuration, dict):
        configuration = {}
    expected_context = configuration.get("llm_requested_context_window")
    if expected_context is None:
        expected_context = configuration.get("context_window")
    actual_context = observed.get("reported_context_window")
    if (
        isinstance(expected_context, int)
        and not isinstance(expected_context, bool)
        and actual_context != expected_context
    ):
        return execution_failure(
            kind="subject_context_mismatch",
            message=(
                f"Agent Zero model configuration reported context "
                f"{actual_context!r}; runtime baseline requests "
                f"{expected_context!r}."
            ),
        )

    expected_utility = configuration.get("llm_utility_model")
    actual_utility = observed.get("reported_utility_model")
    if isinstance(expected_utility, str) and actual_utility != expected_utility:
        return execution_failure(
            kind="subject_utility_model_mismatch",
            message=(
                f"Agent Zero utility model configuration reported "
                f"{actual_utility!r}; runtime baseline requires "
                f"{expected_utility!r}."
            ),
        )

    expected_utility_context = configuration.get(
        "llm_utility_requested_context_window"
    )
    actual_utility_context = observed.get("reported_utility_context_window")
    if (
        isinstance(expected_utility_context, int)
        and not isinstance(expected_utility_context, bool)
        and actual_utility_context != expected_utility_context
    ):
        return execution_failure(
            kind="subject_utility_context_mismatch",
            message=(
                f"Agent Zero utility context configuration reported "
                f"{actual_utility_context!r}; runtime baseline requests "
                f"{expected_utility_context!r}."
            ),
        )
    return None


def _model_observation_fields(
    response: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    config = response.get("config")
    if not isinstance(config, dict):
        return {}
    chat = config.get("chat_model")
    utility = config.get("utility_model")
    if not isinstance(chat, dict):
        chat = {}
    if not isinstance(utility, dict):
        utility = {}
    return {
        "reported_model": chat.get("name"),
        "reported_context_window": chat.get("ctx_length"),
        "reported_utility_model": utility.get("name"),
        "reported_utility_context_window": utility.get("ctx_length"),
        "reported_model_source": "agent_zero_model_config_api",
    }


def _subject_provenance(
    *,
    subject: dict[str, Any],
    context_id: str,
    agent_profile: str,
    model_observation: dict[str, Any] | None,
) -> dict[str, Any]:
    agent = dict(subject.get("agent", {}))
    model = dict(subject.get("model", {}))
    configuration = dict(subject.get("configuration", {}))
    configuration.update(
        {
            "agent_zero_context_id": context_id,
            "agent_zero_agent_profile": agent_profile,
            "execution_interface": "external_api_with_webui_setup",
        }
    )
    observed = _model_observation_fields(model_observation)
    configuration.update(observed)
    return {
        "agent": agent,
        "model": model,
        "configuration": configuration,
    }


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _parse_failure(kind: str, message: str) -> _parsed_run:
    return _parsed_run(
        evidence=(),
        subject_provenance=None,
        failure=execution_failure(kind=kind, message=message),
    )


def _failure_result(kind: str, message: str) -> adapter_execution_result:
    return adapter_execution_result(
        termination="harness_error",
        evidence=(),
        subject_provenance=None,
        failure=execution_failure(kind=kind, message=message),
    )


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise agent_zero_adapter_error(f"Agent Zero {label} is missing.")
    return value
