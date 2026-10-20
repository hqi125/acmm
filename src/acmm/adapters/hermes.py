from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Mapping

from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    execution_failure,
)


@dataclass(frozen=True)
class hermes_adapter_config:
    command_prefix: tuple[str, ...] = ()
    executable: str = "hermes"
    toolset: str = "acmm"
    provider: str | None = None
    reasoning: str | None = None
    source: str = "tool"
    subprocess_grace_seconds: float = 15.0
    extra_environment: Mapping[str, str] | None = None
    working_directory: Path | None = None
    docker_container: str | None = None


class hermes_adapter:
    def __init__(
        self,
        config: hermes_adapter_config | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._config = config or hermes_adapter_config()
        self._runner = runner or subprocess.run

    def execute(
        self,
        subject: dict[str, Any],
        scenario: dict[str, Any],
        environment: Any,
        termination: dict[str, Any],
    ) -> adapter_execution_result:
        try:
            runtime_environment = _runtime_environment_variables(environment)
            command, process_environment = self._build_chat_invocation(
                scenario=scenario,
                termination=termination,
                runtime_environment=runtime_environment,
            )
        except Exception as exc:
            return _failure_result(
                kind="adapter_configuration_error",
                message=str(exc) or exc.__class__.__name__,
            )

        process_timeout = _process_timeout(
            termination,
            self._config.subprocess_grace_seconds,
        )

        try:
            completed = self._run(
                command,
                process_environment,
                process_timeout,
            )
        except subprocess.TimeoutExpired:
            return _failure_result(
                kind="adapter_process_timeout",
                message=(
                    "Hermes process exceeded the harness timeout after its "
                    "configured run budget."
                ),
            )
        except OSError as exc:
            return _failure_result(
                kind="adapter_process_error",
                message=str(exc) or exc.__class__.__name__,
            )

        session_id = _session_id_from_stderr(completed.stderr)

        if session_id is None:
            detail = completed.stderr.strip()

            return _failure_result(
                kind="missing_session_id",
                message=(
                    "Hermes quiet one-shot execution did not report a session_id."
                    + (f" Hermes stderr: {detail}" if detail else "")
                ),
            )

        try:
            export_command = self._build_export_invocation(session_id)
            exported = self._run(
                export_command,
                process_environment,
                process_timeout,
            )
        except subprocess.TimeoutExpired:
            return _failure_result(
                kind="session_export_timeout",
                message=(
                    "Hermes session export exceeded the harness subprocess "
                    "timeout."
                ),
            )
        except OSError as exc:
            return _failure_result(
                kind="session_export_process_error",
                message=str(exc) or exc.__class__.__name__,
            )

        if exported.returncode != 0:
            detail = exported.stderr.strip() or exported.stdout.strip()

            return _failure_result(
                kind="session_export_failed",
                message=(
                    f"Hermes session export exited with status "
                    f"{exported.returncode}."
                    + (f" {detail}" if detail else "")
                ),
            )

        parsed = _parse_session_export(
            exported.stdout,
            scenario["task"]["prompt"],
            completed.stdout,
            session_id,
        )

        if parsed.failure is not None:
            return adapter_execution_result(
                termination="harness_error",
                evidence=parsed.evidence,
                subject_provenance=None,
                failure=parsed.failure,
            )

        expected_model = subject["model"]["name"]

        if parsed.model != expected_model:
            return adapter_execution_result(
                termination="harness_error",
                evidence=parsed.evidence,
                subject_provenance=_subject_provenance(
                    subject,
                    parsed.model,
                    session_id,
                ),
                failure=execution_failure(
                    kind="subject_model_mismatch",
                    message=(
                        f"Hermes session reported model {parsed.model!r}; "
                        f"Contract subject requires {expected_model!r}."
                    ),
                ),
            )

        if completed.returncode != 0:
            termination_kind = _termination_from_failed_process(
                completed,
                parsed.session,
            )

            if termination_kind in {"timeout", "max_steps"}:
                return adapter_execution_result(
                    termination=termination_kind,
                    evidence=parsed.evidence,
                    subject_provenance=_subject_provenance(
                        subject,
                        parsed.model,
                        session_id,
                    ),
                )

            detail = completed.stderr.strip()

            return adapter_execution_result(
                termination="harness_error",
                evidence=parsed.evidence,
                subject_provenance=_subject_provenance(
                    subject,
                    parsed.model,
                    session_id,
                ),
                failure=execution_failure(
                    kind="hermes_run_failed",
                    message=(
                        detail
                        or f"Hermes exited with status {completed.returncode}."
                    ),
                ),
            )

        return adapter_execution_result(
            termination="agent_completed",
            evidence=parsed.evidence,
            subject_provenance=_subject_provenance(
                subject,
                parsed.model,
                session_id,
            ),
        )

    def _run(
        self,
        command: list[str],
        environment: dict[str, str],
        process_timeout: float | None,
    ) -> subprocess.CompletedProcess[str]:
        return self._runner(
            command,
            cwd=self._config.working_directory,
            env=environment,
            text=True,
            capture_output=True,
            timeout=process_timeout,
            check=False,
        )

    def _build_chat_invocation(
        self,
        scenario: dict[str, Any],
        termination: dict[str, Any],
        runtime_environment: Mapping[str, str],
    ) -> tuple[list[str], dict[str, str]]:
        prompt = scenario["task"]["prompt"]
        max_steps = termination["max_steps"]
        timeout_seconds = termination.get("timeout_seconds")

        environment = os.environ.copy()

        if self._config.extra_environment is not None:
            environment.update(self._config.extra_environment)

        environment.update(runtime_environment)

        hermes_args = [
            self._config.executable,
            "chat",
            "--oneshot",
            "-Q",
            "--toolsets",
            self._config.toolset,
            "--max-turns",
            str(max_steps),
            "--source",
            self._config.source,
            "--query",
            prompt,
        ]

        if self._config.provider is not None:
            hermes_args.extend(
                [
                    "--provider",
                    self._config.provider,
                ]
            )

        if self._config.reasoning is not None:
            hermes_args.extend(
                [
                    "--reasoning",
                    self._config.reasoning,
                ]
            )

        if timeout_seconds is not None:
            hermes_args.extend(
                [
                    "--run-budget",
                    str(timeout_seconds),
                ]
            )

        if self._config.docker_container is None:
            return (
                [*self._config.command_prefix, *hermes_args],
                environment,
            )

        docker_args = [
            *self._config.command_prefix,
            "docker",
            "exec",
            "-i",
        ]

        for name, value in sorted(runtime_environment.items()):
            docker_args.extend(
                [
                    "--env",
                    f"{name}={value}",
                ]
            )

        docker_args.extend(
            [
                self._config.docker_container,
                *hermes_args,
            ]
        )

        return docker_args, environment

    def _build_export_invocation(
        self,
        session_id: str,
    ) -> list[str]:
        hermes_args = [
            self._config.executable,
            "sessions",
            "export",
            "-",
            "--session-id",
            session_id,
            "--format",
            "jsonl",
        ]

        if self._config.docker_container is None:
            return [
                *self._config.command_prefix,
                *hermes_args,
            ]

        return [
            *self._config.command_prefix,
            "docker",
            "exec",
            "-i",
            self._config.docker_container,
            *hermes_args,
        ]


@dataclass(frozen=True)
class _parsed_session:
    evidence: tuple[captured_evidence, ...]
    session: dict[str, Any] | None
    model: str | None
    failure: execution_failure | None


def _parse_session_export(
    stdout: str,
    prompt: str,
    final_stdout: str,
    expected_session_id: str,
) -> _parsed_session:
    sessions: list[dict[str, Any]] = []

    for line_number, raw_line in enumerate(stdout.splitlines(), start=1):
        line = raw_line.strip()

        if not line:
            continue

        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            return _parsed_session(
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
            return _parsed_session(
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
        return _parsed_session(
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
        return _parsed_session(
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
    final_response = final_stdout.strip()

    if not final_response:
        final_response = _last_assistant_text(messages)

    model = _optional_string(session.get("model"))

    if model is None:
        return _parsed_session(
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
        return _parsed_session(
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

    return _parsed_session(
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


def _session_id_from_stderr(stderr: str) -> str | None:
    matches = re.findall(
        r"(?m)^\s*session_id:\s*(\S+)\s*$",
        stderr,
    )

    return matches[-1] if matches else None


def _session_identifier(session: Mapping[str, Any]) -> str | None:
    return (
        _optional_string(session.get("id"))
        or _optional_string(session.get("session_id"))
    )


def _process_timeout(
    termination: Mapping[str, Any],
    grace_seconds: float,
) -> float | None:
    timeout_seconds = termination.get("timeout_seconds")

    if timeout_seconds is None:
        return None

    return float(timeout_seconds) + grace_seconds


def _termination_from_failed_process(
    completed: subprocess.CompletedProcess[str],
    session: Mapping[str, Any] | None,
) -> str:
    parts = [
        completed.stderr,
        completed.stdout,
    ]

    if session is not None:
        for key in (
            "end_reason",
            "failure_reason",
            "error",
            "status",
        ):
            value = session.get(key)

            if value is not None:
                parts.append(str(value))

    detail = " ".join(parts).lower()

    if (
        "max turn" in detail
        or "maximum iteration" in detail
        or "iteration budget" in detail
    ):
        return "max_steps"

    if (
        "run budget" in detail
        or "timeout" in detail
        or "timed out" in detail
    ):
        return "timeout"

    return "harness_error"


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _runtime_environment_variables(
    environment: Any,
) -> dict[str, str]:
    if environment is None:
        return {}

    if not isinstance(environment, Mapping):
        raise ValueError(
            "Hermes adapter environment must be a mapping of environment "
            "variable names to string values."
        )

    variables: dict[str, str] = {}

    for name, value in environment.items():
        if not isinstance(name, str) or not name:
            raise ValueError(
                "Hermes adapter environment variable names must be non-empty "
                "strings."
            )

        if not isinstance(value, str):
            raise ValueError(
                f"Hermes adapter environment variable {name!r} must have a "
                "string value."
            )

        variables[name] = value

    return variables


def _subject_provenance(
    subject: dict[str, Any],
    actual_model: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    agent = dict(subject["agent"])
    model = dict(subject["model"])
    configuration = dict(subject.get("configuration", {}))

    if actual_model is not None:
        configuration["reported_model"] = actual_model

    if session_id is not None:
        configuration["hermes_session_id"] = session_id

    return {
        "agent": agent,
        "model": model,
        "configuration": configuration,
    }


def _failure_result(
    kind: str,
    message: str,
) -> adapter_execution_result:
    return adapter_execution_result(
        termination="harness_error",
        evidence=(),
        subject_provenance=None,
        failure=execution_failure(
            kind=kind,
            message=message,
        ),
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
