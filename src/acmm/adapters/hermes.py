from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
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
            magic_number = _environment_magic_number(environment)
            command, process_environment = self._build_invocation(
                subject=subject,
                scenario=scenario,
                termination=termination,
                magic_number=magic_number,
            )
        except Exception as exc:
            return _failure_result(
                kind="adapter_configuration_error",
                message=str(exc) or exc.__class__.__name__,
            )

        timeout_seconds = termination.get("timeout_seconds")
        process_timeout = None

        if timeout_seconds is not None:
            process_timeout = (
                float(timeout_seconds)
                + self._config.subprocess_grace_seconds
            )

        try:
            completed = self._runner(
                command,
                cwd=self._config.working_directory,
                env=process_environment,
                text=True,
                capture_output=True,
                timeout=process_timeout,
                check=False,
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

        parsed = _parse_stream_json(
            completed.stdout,
            scenario["task"]["prompt"],
        )

        if parsed.failure is not None:
            return adapter_execution_result(
                termination="harness_error",
                evidence=parsed.evidence,
                subject_provenance=None,
                failure=parsed.failure,
            )

        result_event = parsed.result_event

        if result_event is None:
            stderr = completed.stderr.strip()
            detail = f" Hermes stderr: {stderr}" if stderr else ""

            return adapter_execution_result(
                termination="harness_error",
                evidence=parsed.evidence,
                subject_provenance=None,
                failure=execution_failure(
                    kind="missing_result_event",
                    message=(
                        "Hermes stream ended without a terminal result event."
                        + detail
                    ),
                ),
            )

        event_exit_code = result_event.get("exit_code", completed.returncode)

        if completed.returncode != 0 or event_exit_code != 0:
            termination_kind = _termination_from_failed_result(result_event)

            if termination_kind in {"timeout", "max_steps"}:
                return adapter_execution_result(
                    termination=termination_kind,
                    evidence=parsed.evidence,
                    subject_provenance=_subject_provenance(
                        subject,
                        parsed.model,
                        parsed.session_id,
                    ),
                )

            error = result_event.get("error")
            message = (
                str(error)
                if error
                else completed.stderr.strip()
                or f"Hermes exited with status {completed.returncode}."
            )

            return adapter_execution_result(
                termination="harness_error",
                evidence=parsed.evidence,
                subject_provenance=_subject_provenance(
                    subject,
                    parsed.model,
                    parsed.session_id,
                ),
                failure=execution_failure(
                    kind="hermes_run_failed",
                    message=message,
                ),
            )

        return adapter_execution_result(
            termination="agent_completed",
            evidence=parsed.evidence,
            subject_provenance=_subject_provenance(
                subject,
                parsed.model,
                parsed.session_id,
            ),
        )

    def _build_invocation(
        self,
        subject: dict[str, Any],
        scenario: dict[str, Any],
        termination: dict[str, Any],
        magic_number: int,
    ) -> tuple[list[str], dict[str, str]]:
        model = subject["model"]["name"]
        prompt = scenario["task"]["prompt"]
        max_steps = termination["max_steps"]
        timeout_seconds = termination.get("timeout_seconds")

        environment = os.environ.copy()

        if self._config.extra_environment is not None:
            environment.update(self._config.extra_environment)

        environment["ACMM_MAGIC_NUMBER"] = str(magic_number)

        hermes_args = [
            self._config.executable,
            "chat",
            "--oneshot",
            "--format",
            "stream-json",
            "--model",
            model,
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
            "--env",
            f"ACMM_MAGIC_NUMBER={magic_number}",
            self._config.docker_container,
            *hermes_args,
        ]

        return docker_args, environment


@dataclass(frozen=True)
class _parsed_stream:
    evidence: tuple[captured_evidence, ...]
    result_event: dict[str, Any] | None
    session_id: str | None
    model: str | None
    failure: execution_failure | None


def _parse_stream_json(
    stdout: str,
    prompt: str,
) -> _parsed_stream:
    events: list[dict[str, Any]] = []

    for line_number, raw_line in enumerate(stdout.splitlines(), start=1):
        line = raw_line.strip()

        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            return _parsed_stream(
                evidence=(),
                result_event=None,
                session_id=None,
                model=None,
                failure=execution_failure(
                    kind="invalid_hermes_stream",
                    message=(
                        f"Hermes emitted invalid JSON on stdout line "
                        f"{line_number}: {exc.msg}."
                    ),
                ),
            )

        if not isinstance(event, dict):
            return _parsed_stream(
                evidence=(),
                result_event=None,
                session_id=None,
                model=None,
                failure=execution_failure(
                    kind="invalid_hermes_stream",
                    message=(
                        f"Hermes stdout line {line_number} is not a JSON object."
                    ),
                ),
            )

        events.append(event)

    init_event = next(
        (
            event
            for event in events
            if event.get("type") == "system"
            and event.get("subtype") == "init"
        ),
        None,
    )

    result_event = next(
        (
            event
            for event in reversed(events)
            if event.get("type") == "result"
        ),
        None,
    )

    session_id = None
    model = None

    if init_event is not None:
        session_id = _optional_string(init_event.get("session_id"))
        model = _optional_string(init_event.get("model"))

    if result_event is not None and session_id is None:
        session_id = _optional_string(result_event.get("session_id"))

    tool_calls = [
        {
            "name": event.get("name"),
            "input": event.get("input", {}),
            "timestamp": event.get("timestamp"),
        }
        for event in events
        if event.get("type") == "tool_use"
    ]

    tool_results = [
        {
            "name": event.get("name"),
            "output": event.get("output"),
            "duration_ms": event.get("duration_ms"),
            "is_error": bool(event.get("is_error", False)),
            "timestamp": event.get("timestamp"),
        }
        for event in events
        if event.get("type") == "tool_result"
    ]

    text_deltas = [
        str(event.get("text", ""))
        for event in events
        if event.get("type") == "text"
    ]

    final_response = ""

    if result_event is not None:
        final_response = str(result_event.get("text") or "")

    if not final_response:
        final_response = "".join(text_deltas)

    conversation = {
        "session_id": session_id,
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            },
            {
                "role": "assistant",
                "content": final_response,
            },
        ],
        "assistant_text_deltas": text_deltas,
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

    return _parsed_stream(
        evidence=evidence,
        result_event=result_event,
        session_id=session_id,
        model=model,
        failure=None,
    )


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _environment_magic_number(environment: Any) -> int:
    if not isinstance(environment, dict):
        raise ValueError(
            "Hermes magic-number adapter requires a mapping environment."
        )

    value = environment.get("magic_number")

    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(
            "Hermes magic-number environment must provide integer magic_number."
        )

    return value


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


def _termination_from_failed_result(
    result_event: Mapping[str, Any],
) -> str:
    error = str(result_event.get("error") or "").lower()

    if "max turn" in error or "iteration" in error:
        return "max_steps"

    if "budget" in error or "timeout" in error or "timed out" in error:
        return "timeout"

    return "harness_error"


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
