from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Mapping

from acmm.adapters.hermes_session import parse_hermes_session_export

from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    execution_failure,
)


@dataclass(frozen=True)
class hermes_adapter_config:
    command_prefix: tuple[str, ...] = ()
    executable: str = "hermes"
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
        del environment
        try:
            command, process_environment = self._build_chat_invocation(
                scenario=scenario,
                termination=termination,
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

        parsed = parse_hermes_session_export(
            exported.stdout,
            scenario["task"]["prompt"],
            completed.stdout,
            session_id,
            allow_stdout_final_response=completed.returncode == 0,
        )
        evidence = _append_tool_name_repair_evidence(
            parsed.evidence,
            completed.stdout,
            completed.stderr,
        )

        if parsed.failure is not None:
            return adapter_execution_result(
                termination="harness_error",
                evidence=evidence,
                subject_provenance=None,
                failure=parsed.failure,
            )

        expected_model = subject["model"]["name"]

        if parsed.model != expected_model:
            return adapter_execution_result(
                termination="harness_error",
                evidence=evidence,
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
            final_response = _final_response_text(evidence)
            termination_kind = _termination_from_failed_process(
                completed,
                parsed.session,
                final_response,
            )

            if termination_kind in {"timeout", "max_steps"}:
                return adapter_execution_result(
                    termination=termination_kind,
                    evidence=evidence,
                    subject_provenance=_subject_provenance(
                        subject,
                        parsed.model,
                        session_id,
                        process_returncode=completed.returncode,
                    ),
                )

            detail = _failed_process_detail(completed, parsed.session)

            if termination_kind == "provider_error":
                provider_evidence = _downgrade_final_response_evidence(evidence)
                return adapter_execution_result(
                    termination="provider_error",
                    evidence=provider_evidence,
                    subject_provenance=_subject_provenance(
                        subject,
                        parsed.model,
                        session_id,
                        process_returncode=completed.returncode,
                    ),
                    failure=execution_failure(
                        kind="hermes_provider_error",
                        message=(
                            detail
                            or "Hermes model/provider request failed after retries."
                        ),
                    ),
                )

            if final_response:
                return adapter_execution_result(
                    termination="agent_completed",
                    evidence=evidence,
                    subject_provenance=_subject_provenance(
                        subject,
                        parsed.model,
                        session_id,
                        process_returncode=completed.returncode,
                        accepted_nonzero_exit=True,
                    ),
                )

            return adapter_execution_result(
                termination="harness_error",
                evidence=evidence,
                subject_provenance=_subject_provenance(
                    subject,
                    parsed.model,
                    session_id,
                    process_returncode=completed.returncode,
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
            evidence=evidence,
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
    ) -> tuple[list[str], dict[str, str]]:
        prompt = scenario["task"]["prompt"]
        max_steps = termination.get("max_steps")
        timeout_seconds = termination.get("timeout_seconds")

        environment = os.environ.copy()

        if self._config.extra_environment is not None:
            environment.update(self._config.extra_environment)

        hermes_args = [
            self._config.executable,
            "chat",
            "--oneshot",
            "-Q",
        ]

        if max_steps is not None:
            hermes_args.extend(
                [
                    "--max-turns",
                    str(max_steps),
                ]
            )

        hermes_args.extend(
            [
                "--source",
                self._config.source,
                "--query",
                prompt,
            ]
        )

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


def _session_id_from_stderr(stderr: str) -> str | None:
    matches = re.findall(
        r"(?m)^\s*session_id:\s*(\S+)\s*$",
        stderr,
    )

    return matches[-1] if matches else None


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
    final_response: str = "",
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

    if final_response:
        parts.append(final_response)

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

    if _contains_provider_failure(detail):
        return "provider_error"

    return "harness_error"




def _downgrade_final_response_evidence(
    evidence: tuple[captured_evidence, ...],
) -> tuple[captured_evidence, ...]:
    if not any(item.id == "final_response" for item in evidence):
        return evidence

    realized: list[captured_evidence] = []
    partial_present = any(item.id == "partial_assistant_text" for item in evidence)
    for item in evidence:
        if item.id != "final_response":
            realized.append(item)
            continue
        if item.content and not partial_present:
            realized.append(
                captured_evidence(
                    id="partial_assistant_text",
                    channel="diagnostic",
                    artifact="diagnostics/partial_assistant_text.txt",
                    content=item.content,
                )
            )
            partial_present = True

    return tuple(realized)


_TOOL_NAME_REPAIR_PATTERN = re.compile(
    r"Auto-repaired tool name:\s*['\"]([^'\"]+)['\"]\s*->\s*['\"]([^'\"]+)['\"]",
    flags=re.IGNORECASE,
)


def _append_tool_name_repair_evidence(
    evidence: tuple[captured_evidence, ...],
    stdout: str,
    stderr: str,
) -> tuple[captured_evidence, ...]:
    repairs: list[dict[str, object]] = []
    ordinal = 0

    for source, text in (("stdout", stdout), ("stderr", stderr)):
        for original_name, repaired_name in _TOOL_NAME_REPAIR_PATTERN.findall(text):
            ordinal += 1
            repairs.append(
                {
                    "ordinal": ordinal,
                    "source": source,
                    "original_name": original_name,
                    "repaired_name": repaired_name,
                }
            )

    if not repairs:
        return evidence

    return evidence + (
        captured_evidence(
            id="tool_name_repairs",
            channel="diagnostic",
            artifact="diagnostics/tool_name_repairs.json",
            content=_json_bytes(
                {
                    "source": "hermes_runtime",
                    "repairs": repairs,
                }
            ),
        ),
    )


def _contains_provider_failure(detail: str) -> bool:
    return bool(
        re.search(
            r"api call failed after\s+\d+\s+retries?:\s*http\s+(?:429|5\d\d)\b",
            detail,
            flags=re.IGNORECASE,
        )
    )


def _failed_process_detail(
    completed: subprocess.CompletedProcess[str],
    session: Mapping[str, Any] | None,
) -> str:
    parts: list[str] = []

    for value in (completed.stdout, completed.stderr):
        text = value.strip()
        if text:
            parts.append(text)

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

    return "\n".join(parts).strip()


def _final_response_text(
    evidence: tuple[Any, ...],
) -> str:
    for artifact in evidence:
        if getattr(artifact, "id", None) != "final_response":
            continue

        content = getattr(artifact, "content", b"")
        if isinstance(content, bytes):
            try:
                return content.decode("utf-8").strip()
            except UnicodeDecodeError:
                return ""

        return str(content).strip()

    return ""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")



def _subject_provenance(
    subject: dict[str, Any],
    actual_model: str | None,
    session_id: str | None,
    *,
    process_returncode: int | None = None,
    accepted_nonzero_exit: bool = False,
) -> dict[str, Any]:
    agent = dict(subject["agent"])
    model = dict(subject["model"])
    configuration = dict(subject.get("configuration", {}))

    if actual_model is not None:
        configuration["reported_model"] = actual_model

    if session_id is not None:
        configuration["hermes_session_id"] = session_id

    if process_returncode is not None:
        configuration["hermes_process_returncode"] = process_returncode

    if accepted_nonzero_exit:
        configuration["hermes_nonzero_exit_accepted"] = True

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
