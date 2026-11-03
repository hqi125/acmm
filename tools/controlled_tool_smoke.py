from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from acmm.runtime.interfaces import adapter_execution_result
from acmm_infra.profiles.llm import (
    load_llm_baseline,
    primary_requested_context_window,
    runtime_provenance_configuration,
)


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


@dataclass(frozen=True)
class controlled_tool_smoke_assessment:
    passed: bool
    detail: str
    final_response: str | None
    tool_calls: tuple[dict[str, Any], ...]
    tool_results: tuple[dict[str, Any], ...]
    summary: controlled_tool_smoke_summary | None


def build_subject(
    *,
    subject_id: str,
    agent_name: str,
    agent_version: str,
    infrastructure_subject: str,
    repo_root: str | Path | None = None,
    llm_baseline_id: str | None = None,
) -> dict[str, Any]:
    root = (
        Path(repo_root).expanduser().resolve()
        if repo_root is not None
        else Path(__file__).resolve().parents[1]
    )
    baseline = load_llm_baseline(root, llm_baseline_id)
    requested_context = primary_requested_context_window(
        baseline,
        infrastructure_subject,
    )
    configuration = {
        "context_window": (
            requested_context
            if requested_context is not None
            else baseline.minimum_context_window
        ),
        **runtime_provenance_configuration(
            baseline,
            infrastructure_subject,
        ),
    }
    return {
        "id": subject_id,
        "agent": {
            "name": agent_name,
            "version": agent_version,
        },
        "model": {
            "name": baseline.model_name,
            "provider": baseline.logical_provider,
        },
        "configuration": configuration,
    }


def build_scenario(*, scenario_id: str) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "task": {
            "prompt": _PROMPT,
        },
    }


def build_environment() -> dict[str, str]:
    tool_config = {
        "magic_number": _EXPECTED_MAGIC_NUMBER,
        "retrieval_mode": "success",
        "addition_mode": "success",
    }
    return {
        "ACMM_MAGIC_NUMBER": str(_EXPECTED_MAGIC_NUMBER),
        "ACMM_RETRIEVAL_MODE": "success",
        "ACMM_ADDITION_MODE": "success",
        "ACMM_TOOL_CONFIG_JSON": json.dumps(tool_config, separators=(",", ":")),
        "ACMM_TOOL_BASELINE_JSON": json.dumps(tool_config, separators=(",", ":")),
    }


def build_termination() -> dict[str, int]:
    return {
        "timeout_seconds": 120,
    }


def assess_result(result: adapter_execution_result) -> controlled_tool_smoke_assessment:
    if result.failure is not None:
        return controlled_tool_smoke_assessment(
            passed=False,
            detail=f"{result.failure.kind}: {result.failure.message}",
            final_response=None,
            tool_calls=(),
            tool_results=(),
            summary=None,
        )

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
        return controlled_tool_smoke_assessment(
            passed=False,
            detail=str(error),
            final_response=None,
            tool_calls=(),
            tool_results=(),
            summary=None,
        )

    return controlled_tool_smoke_assessment(
        passed=True,
        detail="required dependency path and final result satisfied",
        final_response=final_response,
        tool_calls=tuple(tool_calls),
        tool_results=tuple(tool_results),
        summary=summary,
    )


def print_and_check_result(result: adapter_execution_result) -> int:
    print(f"termination: {result.termination}")
    assessment = assess_result(result)
    if not assessment.passed:
        if result.failure is not None:
            print(f"failure: {assessment.detail}")
        else:
            print(f"smoke check failure: {assessment.detail}")
        print("smoke: FAIL")
        return 1

    assert assessment.final_response is not None
    assert assessment.summary is not None
    print("final response:")
    print(assessment.final_response)
    print("tool calls:")
    print(json.dumps(assessment.tool_calls, indent=2))
    print("tool results:")
    print(json.dumps(assessment.tool_results, indent=2))
    print("checks:")
    print("  required dependency path: PASS")
    print("  final result: PASS")
    print("observations:")
    print(f"  controlled tool calls: {assessment.summary.tool_call_count}")
    print(
        "  redundant controlled tool calls: "
        f"{assessment.summary.redundant_tool_call_count}"
    )
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
