from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from acmm.runtime.interfaces import adapter_execution_result


_PROMPT = (
    "Obtain the laboratory magic number using get_magic_number, then "
    "use add_numbers to add 13. Report the result."
)
_EXPECTED_MAGIC_NUMBER = 87
_EXPECTED_ADDEND = 13
_EXPECTED_RESULT = 100
_ALLOWED_TOOLS = {"get_magic_number", "add_numbers"}


@dataclass(frozen=True)
class controlled_tool_smoke_summary:
    tool_call_count: int
    redundant_tool_call_count: int


def build_subject(
    *,
    subject_id: str,
    agent_name: str,
    agent_version: str,
) -> dict[str, Any]:
    return {
        "id": subject_id,
        "agent": {
            "name": agent_name,
            "version": agent_version,
        },
        "model": {
            "name": "nemotron-3-nano:30b-cloud",
            "provider": "ollama_cloud",
        },
        "configuration": {
            "context_window": 64000,
        },
    }


def build_scenario(*, scenario_id: str) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "task": {
            "prompt": _PROMPT,
        },
    }


def build_environment() -> dict[str, str]:
    return {
        "ACMM_MAGIC_NUMBER": str(_EXPECTED_MAGIC_NUMBER),
        "ACMM_RETRIEVAL_MODE": "success",
        "ACMM_ADDITION_MODE": "success",
    }


def build_termination() -> dict[str, int]:
    return {
        "timeout_seconds": 120,
    }


def print_and_check_result(result: adapter_execution_result) -> int:
    print(f"termination: {result.termination}")
    if result.failure is not None:
        print(f"failure: {result.failure.kind}: {result.failure.message}")
        print("smoke: FAIL")
        return 1

    try:
        evidence = {item.id: item for item in result.evidence}
        final_response = evidence["final_response"].content.decode("utf-8")
        tool_calls = _load_json_list(evidence["tool_calls"].content, "tool_calls")
        tool_results = _load_json_list(evidence["tool_results"].content, "tool_results")
        summary = _check_success_path(
            result.termination,
            final_response,
            tool_calls,
            tool_results,
        )
    except (KeyError, UnicodeDecodeError, ValueError, TypeError) as error:
        print(f"smoke check failure: {error}")
        print("smoke: FAIL")
        return 1

    print("final response:")
    print(final_response)
    print("tool calls:")
    print(json.dumps(tool_calls, indent=2))
    print("tool results:")
    print(json.dumps(tool_results, indent=2))
    print("checks:")
    print("  required dependency path: PASS")
    print("  final result: PASS")
    print("observations:")
    print(f"  controlled tool calls: {summary.tool_call_count}")
    print(f"  redundant controlled tool calls: {summary.redundant_tool_call_count}")
    print("  upstream model requests: observe externally; not a pass criterion")
    print("smoke: PASS")
    return 0


def _load_json_list(content: bytes, channel: str) -> list[dict[str, Any]]:
    parsed = json.loads(content)
    if not isinstance(parsed, list):
        raise ValueError(f"{channel} evidence must be a JSON array")
    if not all(isinstance(item, dict) for item in parsed):
        raise ValueError(f"{channel} evidence entries must be JSON objects")
    return parsed


def _check_success_path(
    termination: str,
    final_response: str,
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> controlled_tool_smoke_summary:
    if termination != "agent_completed":
        raise ValueError(f"expected agent_completed termination, got {termination!r}")

    unknown_tools = sorted(
        {
            name
            for call in tool_calls
            if isinstance((name := call.get("name")), str)
            and name not in _ALLOWED_TOOLS
        }
    )
    if unknown_tools:
        raise ValueError(f"unexpected Tool calls observed: {unknown_tools}")

    results_by_id = {
        result.get("id"): result
        for result in tool_results
        if isinstance(result.get("id"), str)
    }

    retrieval_index = _find_successful_retrieval(tool_calls, results_by_id)
    addition_index = _find_successful_addition(
        tool_calls,
        results_by_id,
        start_index=retrieval_index + 1,
    )
    if addition_index <= retrieval_index:
        raise ValueError("add_numbers did not follow get_magic_number")

    if str(_EXPECTED_RESULT) not in final_response:
        raise ValueError(
            f"final response does not report expected result {_EXPECTED_RESULT}"
        )

    return controlled_tool_smoke_summary(
        tool_call_count=len(tool_calls),
        redundant_tool_call_count=max(0, len(tool_calls) - 2),
    )


def _find_successful_retrieval(
    tool_calls: list[dict[str, Any]],
    results_by_id: dict[Any, dict[str, Any]],
) -> int:
    for index, call in enumerate(tool_calls):
        if call.get("name") != "get_magic_number" or call.get("input") != {}:
            continue
        result = results_by_id.get(call.get("id"))
        if result is None or result.get("is_error") is not False:
            continue
        output = _load_tool_output(result)
        if output.get("magic_number") == _EXPECTED_MAGIC_NUMBER:
            return index
    raise ValueError("required successful get_magic_number -> 87 step was not observed")


def _find_successful_addition(
    tool_calls: list[dict[str, Any]],
    results_by_id: dict[Any, dict[str, Any]],
    *,
    start_index: int,
) -> int:
    expected_input = {
        "a": _EXPECTED_MAGIC_NUMBER,
        "b": _EXPECTED_ADDEND,
    }
    for index in range(start_index, len(tool_calls)):
        call = tool_calls[index]
        if call.get("name") != "add_numbers" or call.get("input") != expected_input:
            continue
        result = results_by_id.get(call.get("id"))
        if result is None or result.get("is_error") is not False:
            continue
        output = _load_tool_output(result)
        if output.get("result") == _EXPECTED_RESULT:
            return index
    raise ValueError("required successful add_numbers(87, 13) -> 100 step was not observed")


def _load_tool_output(result: dict[str, Any]) -> dict[str, Any]:
    output = result.get("output")
    if not isinstance(output, str):
        raise ValueError("Tool result output must be a JSON string")
    parsed = json.loads(output)
    if not isinstance(parsed, dict):
        raise ValueError("Tool result output must decode to a JSON object")
    return parsed
