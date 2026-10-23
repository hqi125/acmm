from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol
from uuid import uuid4

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

    def update_plugin_config(
        self,
        plugin_name: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
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
    plugin_name: str = "acmm_tools"
    agent_profile: str = "acmm"
    request_grace_seconds: float = 15.0
    context_lifetime_hours: float = 1.0
    log_length: int = 1000
    terminate_context_after_run: bool = True
    restore_tool_baseline_after_run: bool = True


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
        tool_config_applied = False

        try:
            runtime_config = _runtime_tool_config(environment)
            api_key = self._client.get_external_api_token()
            self._client.update_plugin_config(
                self._config.plugin_name,
                runtime_config,
            )
            tool_config_applied = True

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

            response = self._client.send_message(
                api_key,
                context_id=context_id,
                message=prompt,
                agent_profile=self._config.agent_profile,
                lifetime_hours=self._config.context_lifetime_hours,
                timeout_seconds=_adapter_timeout(
                    termination,
                    self._config.request_grace_seconds,
                ),
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
                plugin_name=self._config.plugin_name,
                agent_profile=self._config.agent_profile,
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
            return _failure_result(
                "adapter_process_timeout",
                str(exc) or "Agent Zero execution exceeded the run timeout.",
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

            if self._config.restore_tool_baseline_after_run and tool_config_applied:
                try:
                    self._client.update_plugin_config(
                        self._config.plugin_name,
                        _baseline_tool_config(),
                    )
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
    plugin_name: str,
    agent_profile: str,
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
        plugin_name=plugin_name,
    )
    provenance = _subject_provenance(
        subject=subject,
        context_id=context_id,
        agent_profile=agent_profile,
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

    if not final_response:
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
    plugin_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    conversation: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": prompt,
        }
    ]
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict) or item.get("type") != "tool":
            continue

        kvps = item.get("kvps")
        if not isinstance(kvps, dict):
            kvps = {}

        raw_name = kvps.get("_tool_name")
        if not isinstance(raw_name, str) or not raw_name:
            continue

        name = _normalize_tool_name(raw_name, plugin_name)
        if name not in {"get_magic_number", "add_numbers"}:
            continue

        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"agent-zero-tool-{item.get('no', len(tool_calls))}"

        arguments = {
            str(key): value
            for key, value in kvps.items()
            if isinstance(key, str) and not key.startswith("_")
        }
        content = item.get("content")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        timestamp = item.get("timestamp")
        is_error = _tool_output_has_error(content)

        tool_calls.append(
            {
                "id": call_id,
                "name": name,
                "input": arguments,
                "timestamp": timestamp,
            }
        )
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

    conversation.append(
        {
            "role": "assistant",
            "content": final_response,
        }
    )
    return conversation, tool_calls, tool_results


def _normalize_tool_name(name: str, plugin_name: str) -> str:
    prefixes = (
        f"plugin:{plugin_name}:",
        f"{plugin_name}:",
    )
    for prefix in prefixes:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _runtime_tool_config(environment: Any) -> dict[str, Any]:
    if not isinstance(environment, dict):
        raise agent_zero_adapter_error(
            "Agent Zero controlled-tool execution requires a fixture environment mapping."
        )

    try:
        magic_number = int(environment["ACMM_MAGIC_NUMBER"])
        retrieval_mode = str(environment["ACMM_RETRIEVAL_MODE"])
        addition_mode = str(environment["ACMM_ADDITION_MODE"])
    except (KeyError, TypeError, ValueError) as exc:
        raise agent_zero_adapter_error(
            "Agent Zero fixture environment is missing controlled tool values."
        ) from exc

    for name, value in (
        ("retrieval_mode", retrieval_mode),
        ("addition_mode", addition_mode),
    ):
        if value not in {"success", "failure"}:
            raise agent_zero_adapter_error(
                f"Agent Zero {name} must be 'success' or 'failure'."
            )

    return {
        "magic_number": magic_number,
        "retrieval_mode": retrieval_mode,
        "addition_mode": addition_mode,
    }


def _baseline_tool_config() -> dict[str, Any]:
    return {
        "magic_number": 87,
        "retrieval_mode": "success",
        "addition_mode": "success",
    }


def _adapter_timeout(
    termination: dict[str, Any],
    grace_seconds: float,
) -> float:
    timeout = termination.get("timeout_seconds")
    if timeout is None:
        return 600.0
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise agent_zero_adapter_error(
            "termination.timeout_seconds must be a positive number."
        )
    return float(timeout) + grace_seconds


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


def _subject_provenance(
    *,
    subject: dict[str, Any],
    context_id: str,
    agent_profile: str,
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
