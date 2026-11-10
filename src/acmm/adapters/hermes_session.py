from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from acmm.runtime.interfaces import captured_evidence, execution_failure


@dataclass(frozen=True)
class hermes_session_parse_result:
    evidence: tuple[captured_evidence, ...]
    session: dict[str, Any] | None
    model: str | None
    failure: execution_failure | None


def parse_hermes_session_export(
    stdout: str,
    prompt: str,
    final_stdout: str,
    expected_session_id: str,
) -> hermes_session_parse_result:
    sessions: list[dict[str, Any]] = []

    for line_number, raw_line in enumerate(stdout.splitlines(), start=1):
        line = raw_line.strip()

        if not line:
            continue

        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            return hermes_session_parse_result(
                evidence=(),
                session=None,
                model=None,
                failure=execution_failure(
                    kind="invalid_session_export",
                    message=(
                        f"Hermes session export emitted invalid JSON on line "
                        f"{line_number}: {exc.msg}."
                    ),
                ),
            )

        if not isinstance(value, dict):
            return hermes_session_parse_result(
                evidence=(),
                session=None,
                model=None,
                failure=execution_failure(
                    kind="invalid_session_export",
                    message=(
                        f"Hermes session export line {line_number} is not "
                        "a JSON object."
                    ),
                ),
            )

        sessions.append(value)

    session = next(
        (
            item
            for item in sessions
            if _session_identifier(item) == expected_session_id
        ),
        None,
    )

    if session is None:
        return hermes_session_parse_result(
            evidence=(),
            session=None,
            model=None,
            failure=execution_failure(
                kind="missing_exported_session",
                message=(
                    f"Hermes session export did not contain session "
                    f"{expected_session_id!r}."
                ),
            ),
        )

    messages = session.get("messages")

    if not isinstance(messages, list) or not all(
        isinstance(item, dict)
        for item in messages
    ):
        return hermes_session_parse_result(
            evidence=(),
            session=session,
            model=_optional_string(session.get("model")),
            failure=execution_failure(
                kind="invalid_session_messages",
                message=(
                    "Hermes exported session does not contain a JSON message "
                    "array."
                ),
            ),
        )

    tool_calls, call_names = _normalize_tool_calls(messages)
    tool_results = _normalize_tool_results(messages, call_names)
    final_response = _last_assistant_text(messages).strip()

    if not final_response:
        final_response = final_stdout.strip()

    model = _optional_string(session.get("model"))

    if model is None:
        return hermes_session_parse_result(
            evidence=(),
            session=session,
            model=None,
            failure=execution_failure(
                kind="missing_session_model",
                message="Hermes exported session does not report its model.",
            ),
        )

    conversation = {
        "session_id": expected_session_id,
        "model": model,
        "messages": messages,
    }

    evidence = (
        captured_evidence(
            id="conversation",
            channel="conversation",
            artifact="evidence/conversation.json",
            content=_json_bytes(conversation),
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

    if not _transcript_contains_prompt(messages, prompt):
        return hermes_session_parse_result(
            evidence=evidence,
            session=session,
            model=model,
            failure=execution_failure(
                kind="session_prompt_mismatch",
                message=(
                    "Hermes exported session does not contain the Contract "
                    "scenario prompt."
                ),
            ),
        )

    return hermes_session_parse_result(
        evidence=evidence,
        session=session,
        model=model,
        failure=None,
    )


def _normalize_tool_calls(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    normalized: list[dict[str, Any]] = []
    names_by_id: dict[str, str] = {}

    for message in messages:
        raw_calls = _json_value(message.get("tool_calls"))

        if not isinstance(raw_calls, list):
            continue

        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                continue

            function = raw_call.get("function")
            function_data = function if isinstance(function, dict) else {}

            name = (
                _optional_string(function_data.get("name"))
                or _optional_string(raw_call.get("name"))
            )

            if name is None:
                continue

            arguments = (
                function_data.get("arguments")
                if "arguments" in function_data
                else raw_call.get("arguments", raw_call.get("input", {}))
            )
            parsed_input = _json_value(arguments)

            if parsed_input is None:
                parsed_input = {}

            call_id = (
                _optional_string(raw_call.get("id"))
                or _optional_string(raw_call.get("tool_call_id"))
            )

            transport_name = name
            underlying = _unwrap_tool_search_bridge(name, parsed_input)

            if underlying is not None:
                name, parsed_input = underlying

            if call_id is not None:
                names_by_id[call_id] = name

            item = {
                "id": call_id,
                "name": name,
                "input": parsed_input,
                "timestamp": message.get("timestamp"),
            }

            if transport_name != name:
                item["transport_name"] = transport_name

            normalized.append(item)

    return normalized, names_by_id


def _normalize_tool_results(
    messages: list[dict[str, Any]],
    names_by_id: Mapping[str, str],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []

    for message in messages:
        if message.get("role") != "tool":
            continue

        call_id = (
            _optional_string(message.get("tool_call_id"))
            or _optional_string(message.get("call_id"))
        )
        normalized_name = (
            names_by_id.get(call_id)
            if call_id is not None
            else None
        )
        persisted_name = (
            _optional_string(message.get("tool_name"))
            or _optional_string(message.get("name"))
        )
        name = normalized_name or persisted_name

        if name is None:
            continue

        output = _message_content(message.get("content"))
        is_error = bool(message.get("is_error", False))

        if not is_error:
            status = str(message.get("status") or "").lower()
            is_error = status in {"error", "failed", "failure"}

        if not is_error:
            is_error = _tool_output_has_error(output)

        is_error = _normalize_terminal_process_error(
            name=name,
            output=output,
            default=is_error,
        )

        item = {
            "id": call_id,
            "name": name,
            "output": output,
            "is_error": is_error,
            "timestamp": message.get("timestamp"),
        }

        if persisted_name is not None and persisted_name != name:
            item["transport_name"] = persisted_name

        normalized.append(item)

    return normalized



def _normalize_terminal_process_error(
    *,
    name: str,
    output: Any,
    default: bool,
) -> bool:
    value = _decode_tool_output_object(output)
    if not isinstance(value, dict):
        return default

    if name == "terminal":
        exit_code = value.get("exit_code")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            return exit_code != 0 or _explicit_error_value(value.get("error"))

    if name == "process_manage":
        exit_code = value.get("exit_code")
        if value.get("status") == "exited" and isinstance(exit_code, int):
            return exit_code != 0

    return default


def _decode_tool_output_object(output: Any) -> dict[str, Any] | None:
    value = output
    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = _embedded_json_object(value)
            break
    return value if isinstance(value, dict) else None


def _tool_output_has_error(output: Any) -> bool:
    value = output

    if isinstance(value, str):
        stripped = value.strip()
        if (
            stripped.startswith("Tool '")
            and " does not exist. Available tools:" in stripped
        ):
            return True

    for _ in range(2):
        if not isinstance(value, str):
            break

        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = _embedded_json_object(value)
            break

    if not isinstance(value, dict):
        return False
    return value.get("ok") is False or _explicit_error_value(value.get("error"))


def _explicit_error_value(value: Any) -> bool:
    return value is not None and value is not False and value != ""


def _embedded_json_object(value: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()

    for index, character in enumerate(value):
        if character != "{":
            continue

        try:
            parsed, _ = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, dict):
            return parsed

    return None


def _unwrap_tool_search_bridge(
    name: str,
    parsed_input: Any,
) -> tuple[str, Any] | None:
    if name != "tool_call" or not isinstance(parsed_input, dict):
        return None

    calls = parsed_input.get("calls")

    if not isinstance(calls, list) or len(calls) != 1:
        return None

    entry = calls[0]

    if not isinstance(entry, dict):
        return None

    underlying_name = _optional_string(entry.get("name"))
    arguments = entry.get("arguments", {})

    if underlying_name is None or not isinstance(arguments, dict):
        return None

    return underlying_name, arguments


def _transcript_contains_prompt(
    messages: list[dict[str, Any]],
    prompt: str,
) -> bool:
    return any(
        message.get("role") == "user"
        and _message_content(message.get("content")).strip() == prompt.strip()
        for message in messages
    )


def _last_assistant_text(
    messages: list[dict[str, Any]],
) -> str:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue

        text = _message_content(message.get("content")).strip()

        if text:
            return text

    return ""


def _message_content(value: Any) -> str:
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        parts: list[str] = []

        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue

            if isinstance(item, dict):
                text = item.get("text")

                if isinstance(text, str):
                    parts.append(text)
                    continue

            parts.append(
                json.dumps(
                    item,
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
            )

        return "\n".join(parts)

    if value is None:
        return ""

    return str(value)


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    stripped = value.strip()

    if not stripped:
        return value

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _session_identifier(session: Mapping[str, Any]) -> str | None:
    return (
        _optional_string(session.get("id"))
        or _optional_string(session.get("session_id"))
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
