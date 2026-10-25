from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Protocol
from uuid import uuid4

from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    execution_failure,
)


class open_webui_adapter_error(RuntimeError):
    pass


class open_webui_adapter_client(Protocol):
    def authenticate_no_auth_admin(self) -> dict[str, Any]:
        ...

    def update_tool_valves(
        self,
        token: str,
        tool_id: str,
        valves: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def create_chat(
        self,
        token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def complete_chat(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> Any:
        ...

    def get_chat(self, token: str, chat_id: str) -> dict[str, Any]:
        ...

    def delete_chat(self, token: str, chat_id: str) -> Any:
        ...


@dataclass(frozen=True)
class open_webui_adapter_config:
    tool_id: str = "acmm_tools"
    delete_chat_after_run: bool = True


class open_webui_adapter:
    def __init__(
        self,
        client: open_webui_adapter_client,
        config: open_webui_adapter_config | None = None,
    ) -> None:
        self._client = client
        self._config = config or open_webui_adapter_config()

    def execute(
        self,
        subject: dict[str, Any],
        scenario: dict[str, Any],
        environment: Any,
        termination: dict[str, Any],
    ) -> adapter_execution_result:
        chat_id: str | None = None
        token: str | None = None
        prompt: str | None = None
        model: str | None = None
        assistant_message_id: str | None = None

        try:
            valves = _runtime_valves(environment)
            session = self._client.authenticate_no_auth_admin()
            token = _required_string(session.get("token"), "authentication token")
            self._client.update_tool_valves(
                token,
                self._config.tool_id,
                valves,
            )

            prompt = _required_string(
                scenario.get("task", {}).get("prompt"),
                "scenario task prompt",
            )
            model = _required_string(
                subject.get("model", {}).get("name"),
                "subject model name",
            )

            user_message_id = str(uuid4())
            assistant_message_id = str(uuid4())
            timestamp = int(time.time())

            created = self._client.create_chat(
                token,
                _chat_create_payload(
                    prompt=prompt,
                    model=model,
                    user_message_id=user_message_id,
                    assistant_message_id=assistant_message_id,
                    timestamp=timestamp,
                ),
            )
            chat_id = _required_string(created.get("id"), "created chat id")

            timeout_seconds = _adapter_timeout(termination)
            self._client.complete_chat(
                token,
                _completion_payload(
                    prompt=prompt,
                    model=model,
                    chat_id=chat_id,
                    user_message_id=user_message_id,
                    assistant_message_id=assistant_message_id,
                    timestamp=timestamp,
                    tool_id=self._config.tool_id,
                ),
                timeout_seconds=timeout_seconds,
            )

            chat = self._client.get_chat(token, chat_id)
            parsed = _parse_completed_chat(
                chat=chat,
                chat_id=chat_id,
                assistant_message_id=assistant_message_id,
                prompt=prompt,
                expected_model=model,
                subject=subject,
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
            if (
                token is None
                or chat_id is None
                or prompt is None
                or model is None
                or assistant_message_id is None
            ):
                return _failure_result(
                    "adapter_process_timeout",
                    str(exc) or "Open WebUI completion exceeded the run timeout.",
                )

            try:
                chat = self._client.get_chat(token, chat_id)
                parsed = _parse_completed_chat(
                    chat=chat,
                    chat_id=chat_id,
                    assistant_message_id=assistant_message_id,
                    prompt=prompt,
                    expected_model=model,
                    subject=subject,
                    require_final_response=False,
                )
            except Exception as capture_exc:
                return _failure_result(
                    "timeout_evidence_capture_failed",
                    str(capture_exc) or capture_exc.__class__.__name__,
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
                self._config.delete_chat_after_run
                and token is not None
                and chat_id is not None
            ):
                try:
                    self._client.delete_chat(token, chat_id)
                except Exception:
                    pass


@dataclass(frozen=True)
class _parsed_chat:
    evidence: tuple[captured_evidence, ...]
    subject_provenance: dict[str, Any] | None
    failure: execution_failure | None


def _chat_create_payload(
    *,
    prompt: str,
    model: str,
    user_message_id: str,
    assistant_message_id: str,
    timestamp: int,
) -> dict[str, Any]:
    return {
        "chat": {
            "title": "ACMM run",
            "models": [model],
            "history": {
                "currentId": assistant_message_id,
                "messages": {
                    user_message_id: {
                        "id": user_message_id,
                        "role": "user",
                        "content": prompt,
                        "timestamp": timestamp,
                        "models": [model],
                        "childrenIds": [assistant_message_id],
                    },
                    assistant_message_id: {
                        "id": assistant_message_id,
                        "role": "assistant",
                        "content": "",
                        "parentId": user_message_id,
                        "childrenIds": [],
                        "model": model,
                        "modelName": model,
                        "modelIdx": 0,
                        "done": False,
                        "timestamp": timestamp + 1,
                    },
                },
            },
        }
    }


def _completion_payload(
    *,
    prompt: str,
    model: str,
    chat_id: str,
    user_message_id: str,
    assistant_message_id: str,
    timestamp: int,
    tool_id: str,
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": True,
        "chat_id": chat_id,
        "id": assistant_message_id,
        "parent_id": user_message_id,
        "user_message": {
            "id": user_message_id,
            "role": "user",
            "content": prompt,
            "parentId": None,
            "childrenIds": [assistant_message_id],
            "timestamp": timestamp,
            "models": [model],
        },
        "params": {
            "function_calling": "native",
            "tool_approval_mode": "full",
        },
        "tool_ids": [tool_id],
        "features": {
            "web_search": False,
            "code_interpreter": False,
            "image_generation": False,
            "memory": False,
        },
        "background_tasks": {
            "title_generation": False,
            "tags_generation": False,
            "follow_up_generation": False,
        },
    }


def _parse_completed_chat(
    *,
    chat: dict[str, Any],
    chat_id: str,
    assistant_message_id: str,
    prompt: str,
    expected_model: str,
    subject: dict[str, Any],
    require_final_response: bool = True,
) -> _parsed_chat:
    chat_data = chat.get("chat")
    history = chat_data.get("history") if isinstance(chat_data, dict) else None
    messages = history.get("messages") if isinstance(history, dict) else None

    if not isinstance(messages, dict):
        return _parse_failure(
            "invalid_chat_history",
            "Open WebUI chat does not contain a history message mapping.",
        )

    assistant = messages.get(assistant_message_id)
    if not isinstance(assistant, dict):
        return _parse_failure(
            "missing_assistant_message",
            "Open WebUI chat does not contain the expected assistant message.",
        )

    actual_model = _optional_string(assistant.get("model")) or expected_model
    provenance = _subject_provenance(
        subject=subject,
        actual_model=actual_model,
        chat_id=chat_id,
    )

    if actual_model != expected_model:
        return _parsed_chat(
            evidence=(),
            subject_provenance=provenance,
            failure=execution_failure(
                kind="subject_model_mismatch",
                message=(
                    f"Open WebUI chat reported model {actual_model!r}; "
                    f"Contract subject requires {expected_model!r}."
                ),
            ),
        )

    final_response = _message_content(assistant.get("content")).strip()
    output = assistant.get("output")

    if not isinstance(output, list):
        output = []

    conversation_messages, tool_calls, tool_results = _normalize_output(
        prompt=prompt,
        output=output,
        final_response=final_response,
        assistant_timestamp=assistant.get("timestamp"),
    )

    evidence = (
        captured_evidence(
            id="conversation",
            channel="conversation",
            artifact="evidence/conversation.json",
            content=_json_bytes(
                {
                    "chat_id": chat_id,
                    "model": actual_model,
                    "messages": conversation_messages,
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
        return _parsed_chat(
            evidence=evidence,
            subject_provenance=provenance,
            failure=execution_failure(
                kind="missing_final_response",
                message="Open WebUI completed the chat without final assistant text.",
            ),
        )

    return _parsed_chat(
        evidence=evidence,
        subject_provenance=provenance,
        failure=None,
    )


def _normalize_output(
    *,
    prompt: str,
    output: list[Any],
    final_response: str,
    assistant_timestamp: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    conversation: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": prompt,
        }
    ]
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    names_by_call_id: dict[str, str] = {}

    for item in output:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")

        if item_type == "function_call":
            call_id = _optional_string(item.get("call_id")) or _optional_string(
                item.get("id")
            )
            name = _optional_string(item.get("name"))

            if call_id is None or name is None:
                continue

            arguments = _json_value(item.get("arguments", {}))
            if not isinstance(arguments, dict):
                arguments = {}

            names_by_call_id[call_id] = name
            tool_calls.append(
                {
                    "id": call_id,
                    "name": name,
                    "input": arguments,
                    "timestamp": assistant_timestamp,
                }
            )
            conversation.append(
                {
                    "role": "assistant",
                    "content": "",
                    "timestamp": assistant_timestamp,
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
            continue

        if item_type == "function_call_output":
            call_id = _optional_string(item.get("call_id"))
            if call_id is None:
                continue

            name = names_by_call_id.get(call_id)
            if name is None:
                continue

            content = _function_output_text(item.get("output"))
            status = str(item.get("status") or "").lower()
            is_error = status in {"error", "failed", "failure"}

            if not is_error:
                is_error = _tool_output_has_error(content)

            tool_results.append(
                {
                    "id": call_id,
                    "name": name,
                    "output": content,
                    "is_error": is_error,
                    "timestamp": assistant_timestamp,
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
                    "timestamp": assistant_timestamp,
                }
            )

    conversation.append(
        {
            "role": "assistant",
            "content": final_response,
            "timestamp": assistant_timestamp,
        }
    )

    return conversation, tool_calls, tool_results


def _function_output_text(value: Any) -> str:
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        parts: list[str] = []

        for item in value:
            if not isinstance(item, dict):
                continue

            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)

        return "\n".join(parts)

    if value is None:
        return ""

    return str(value)


def _runtime_valves(environment: Any) -> dict[str, Any]:
    if not isinstance(environment, dict):
        raise open_webui_adapter_error(
            "Open WebUI controlled-tool execution requires a fixture environment mapping."
        )

    generic_config = environment.get("ACMM_TOOL_CONFIG_JSON")

    if generic_config is not None:
        return _json_object_configuration(
            generic_config,
            "ACMM_TOOL_CONFIG_JSON",
        )

    try:
        magic_number = int(environment["ACMM_MAGIC_NUMBER"])
        retrieval_mode = str(environment["ACMM_RETRIEVAL_MODE"])
        addition_mode = str(environment["ACMM_ADDITION_MODE"])
    except (KeyError, TypeError, ValueError) as exc:
        raise open_webui_adapter_error(
            "Open WebUI fixture environment is missing controlled tool values."
        ) from exc

    for name, value in (
        ("retrieval_mode", retrieval_mode),
        ("addition_mode", addition_mode),
    ):
        if value not in {"success", "failure"}:
            raise open_webui_adapter_error(
                f"Open WebUI {name} must be 'success' or 'failure'."
            )

    return {
        "magic_number": magic_number,
        "retrieval_mode": retrieval_mode,
        "addition_mode": addition_mode,
    }


def _json_object_configuration(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise open_webui_adapter_error(f"{name} must be a JSON object string.")

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise open_webui_adapter_error(
            f"{name} must contain valid JSON."
        ) from exc

    if not isinstance(parsed, dict):
        raise open_webui_adapter_error(f"{name} must contain a JSON object.")

    return parsed


def _adapter_timeout(termination: dict[str, Any]) -> float:
    timeout = termination.get("timeout_seconds")

    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise open_webui_adapter_error(
            "termination.timeout_seconds must be a positive number."
        )

    return float(timeout)


def _subject_provenance(
    *,
    subject: dict[str, Any],
    actual_model: str,
    chat_id: str,
) -> dict[str, Any]:
    agent = dict(subject.get("agent", {}))
    model = dict(subject.get("model", {}))
    configuration = dict(subject.get("configuration", {}))
    configuration.update(
        {
            "reported_model": actual_model,
            "open_webui_chat_id": chat_id,
            "execution_interface": "backend_api_native_tool_loop",
        }
    )

    return {
        "agent": agent,
        "model": model,
        "configuration": configuration,
    }


def _parse_failure(kind: str, message: str) -> _parsed_chat:
    return _parsed_chat(
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
        raise open_webui_adapter_error(f"Open WebUI {label} is missing.")
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _message_content(value: Any) -> str:
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)

    return "" if value is None else str(value)


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _tool_output_has_error(output: str) -> bool:
    value: Any = output

    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            break

    return isinstance(value, dict) and "error" in value


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
