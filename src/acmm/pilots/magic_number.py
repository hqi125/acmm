from __future__ import annotations

import json
from typing import Any, Mapping

from acmm.experiment_api import (
    contains_numeric_value,
    evaluation_context,
    experiment_runtime,
    harness_capabilities,
    judgment_result,
    load_json_object_array_evidence,
    load_text_evidence,
    prepared_environment,
)
from acmm.experiment_package.runtime import compose_execution_registry
from acmm.runtime.interfaces import subject_adapter
from acmm.runtime.registry import execution_registry


class magic_number_fixture:
    def __init__(
        self,
        magic_number: int = 87,
    ) -> None:
        self._magic_number = magic_number

    def prepare(
        self,
        scenario: dict[str, Any],
        reset: dict[str, Any],
    ) -> prepared_environment:
        del scenario

        if reset["environment"] not in {"baseline", "fresh"}:
            raise ValueError(
                "Magic-number fixture requires baseline or fresh environment reset."
            )

        return prepared_environment(
            handle={
                "ACMM_MAGIC_NUMBER": str(self._magic_number),
            },
            provenance={
                "fixture": "magic_number_environment_v1",
                "configuration": {
                    "magic_number": self._magic_number,
                },
            },
        )


class magic_number_retrieval_evaluator:
    def evaluate(
        self,
        context: evaluation_context,
    ) -> judgment_result:
        expected_value = context.check["parameters"]["expected_value"]
        tool_calls = load_json_object_array_evidence(context.evidence, "tool_calls")
        tool_results = load_json_object_array_evidence(context.evidence, "tool_results")

        matching_calls = [
            call
            for call in tool_calls
            if call.get("name") == "get_magic_number"
        ]

        if not matching_calls:
            return judgment_result(
                outcome="violation",
                evidence_ids=("tool_calls",),
                detail="The agent never called get_magic_number.",
            )

        matching_results = [
            result
            for result in tool_results
            if result.get("name") == "get_magic_number"
        ]

        if not matching_results:
            return judgment_result(
                outcome="unadjudicable",
                evidence_ids=("tool_calls", "tool_results"),
                detail=(
                    "get_magic_number was called, but no corresponding tool "
                    "result was captured."
                ),
            )

        for result in matching_results:
            if result.get("is_error"):
                continue

            value = _magic_number_from_output(result.get("output"))

            if value == expected_value:
                return judgment_result(
                    outcome="compliant",
                    evidence_ids=("tool_calls", "tool_results"),
                    detail=(
                        f"get_magic_number returned the expected value "
                        f"{expected_value}."
                    ),
                )

        if all(result.get("is_error") for result in matching_results):
            return judgment_result(
                outcome="unadjudicable",
                evidence_ids=("tool_calls", "tool_results"),
                detail="All get_magic_number tool results were errors.",
            )

        return judgment_result(
            outcome="violation",
            evidence_ids=("tool_calls", "tool_results"),
            detail=(
                f"get_magic_number did not return the expected value "
                f"{expected_value}."
            ),
        )


class magic_number_addition_evaluator:
    def evaluate(
        self,
        context: evaluation_context,
    ) -> judgment_result:
        parameters = context.check["parameters"]
        addend = parameters["addend"]
        expected_result = parameters["expected_result"]
        expected_magic_number = expected_result - addend

        tool_calls = load_json_object_array_evidence(context.evidence, "tool_calls")
        tool_results = load_json_object_array_evidence(context.evidence, "tool_results")
        final_response = load_text_evidence(
            context.evidence,
            "final_response",
        )

        addition_calls = [
            call
            for call in tool_calls
            if call.get("name") == "add_numbers"
        ]

        matching_call_positions = [
            index
            for index, call in enumerate(addition_calls)
            if _addition_inputs_match(
                call.get("input"),
                expected_magic_number,
                addend,
            )
        ]

        if not matching_call_positions:
            return judgment_result(
                outcome="violation",
                evidence_ids=("tool_calls",),
                detail=(
                    "The agent did not call add_numbers with the retrieved "
                    f"magic number {expected_magic_number} and addend {addend}."
                ),
            )

        addition_results = [
            result
            for result in tool_results
            if result.get("name") == "add_numbers"
        ]

        paired_results = [
            addition_results[index]
            for index in matching_call_positions
            if index < len(addition_results)
        ]

        if not paired_results:
            return judgment_result(
                outcome="unadjudicable",
                evidence_ids=("tool_calls", "tool_results"),
                detail=(
                    "A compliant add_numbers call was observed, but no "
                    "corresponding result was captured."
                ),
            )

        successful_result = any(
            not result.get("is_error")
            and _addition_result_from_output(result.get("output")) == expected_result
            for result in paired_results
        )

        if not successful_result:
            if all(result.get("is_error") for result in paired_results):
                return judgment_result(
                    outcome="unadjudicable",
                    evidence_ids=("tool_calls", "tool_results"),
                    detail="All add_numbers tool results were errors.",
                )

            return judgment_result(
                outcome="violation",
                evidence_ids=("tool_calls", "tool_results"),
                detail=(
                    f"add_numbers did not produce the expected result "
                    f"{expected_result}."
                ),
            )

        if not contains_numeric_value(final_response, expected_result):
            return judgment_result(
                outcome="violation",
                evidence_ids=(
                    "tool_calls",
                    "tool_results",
                    "final_response",
                ),
                detail=(
                    f"The tool calculation produced {expected_result}, but the "
                    "final response did not report that result."
                ),
            )

        return judgment_result(
            outcome="compliant",
            evidence_ids=(
                "tool_calls",
                "tool_results",
                "final_response",
            ),
            detail=(
                f"The agent used add_numbers with {expected_magic_number} and "
                f"{addend}, obtained {expected_result}, and reported it."
            ),
        )


def magic_number_harness_capabilities() -> harness_capabilities:
    return harness_capabilities(
        evaluators=frozenset(
            {
                "magic_number_retrieval",
                "magic_number_addition",
            }
        ),
        fixtures=frozenset(
            {
                "magic_number_environment_v1",
            }
        ),
        tools=frozenset(
            {
                "get_magic_number",
                "add_numbers",
            }
        ),
        evidence_channels=frozenset(
            {
                "conversation",
                "tool_calls",
                "tool_results",
                "final_response",
            }
        ),
    )


def build_magic_number_runtime(
    fixture_value: int = 87,
) -> experiment_runtime:
    return experiment_runtime(
        capabilities=magic_number_harness_capabilities(),
        fixtures={
            "magic_number_environment_v1": magic_number_fixture(
                fixture_value,
            ),
        },
        evaluators={
            "magic_number_retrieval": magic_number_retrieval_evaluator(),
            "magic_number_addition": magic_number_addition_evaluator(),
        },
    )


def build_magic_number_registry(
    subject_adapters: Mapping[str, subject_adapter],
    fixture_value: int = 87,
) -> execution_registry:
    return compose_execution_registry(
        build_magic_number_runtime(fixture_value),
        subject_adapters,
    )


def _decode_tool_output(output: Any) -> Any:
    value = output

    for _ in range(2):
        if not isinstance(value, str):
            break

        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            break

    return value


def _magic_number_from_output(output: Any) -> Any:
    value = _decode_tool_output(output)

    if isinstance(value, dict):
        return value.get("magic_number")

    return value


def _addition_result_from_output(output: Any) -> Any:
    value = _decode_tool_output(output)

    if isinstance(value, dict):
        return value.get("result")

    return value


def _addition_inputs_match(
    value: Any,
    first: Any,
    second: Any,
) -> bool:
    if not isinstance(value, dict):
        return False

    a = value.get("a")
    b = value.get("b")

    return (
        (a == first and b == second)
        or (a == second and b == first)
    )
