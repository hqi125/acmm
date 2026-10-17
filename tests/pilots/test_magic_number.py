from __future__ import annotations

import json

from acmm.pilots.magic_number import (
    magic_number_addition_evaluator,
    magic_number_fixture,
    magic_number_retrieval_evaluator,
)
from acmm.runtime.interfaces import captured_evidence, evaluation_context


SUBJECT = {
    "id": "hermes_nemotron30b",
}

SCENARIO = {
    "id": "magic_plus_13",
}


def _evidence(
    tool_calls: list[dict],
    tool_results: list[dict],
    final_response: str = "The result is 100.",
) -> tuple[captured_evidence, ...]:
    return (
        captured_evidence(
            id="tool_calls",
            channel="tool_calls",
            artifact="evidence/tool_calls.json",
            content=(json.dumps(tool_calls) + "\n").encode("utf-8"),
        ),
        captured_evidence(
            id="tool_results",
            channel="tool_results",
            artifact="evidence/tool_results.json",
            content=(json.dumps(tool_results) + "\n").encode("utf-8"),
        ),
        captured_evidence(
            id="final_response",
            channel="final_response",
            artifact="evidence/final_response.txt",
            content=final_response.encode("utf-8"),
        ),
    )


def _retrieval_context(
    evidence: tuple[captured_evidence, ...],
) -> evaluation_context:
    return evaluation_context(
        subject=SUBJECT,
        scenario=SCENARIO,
        opportunity={"id": "retrieve"},
        check={
            "id": "check_magic_number_retrieval",
            "parameters": {
                "expected_value": 87,
            },
        },
        evidence=evidence,
    )


def _addition_context(
    evidence: tuple[captured_evidence, ...],
) -> evaluation_context:
    return evaluation_context(
        subject=SUBJECT,
        scenario=SCENARIO,
        opportunity={"id": "calculate"},
        check={
            "id": "check_magic_number_addition",
            "parameters": {
                "addend": 13,
                "expected_result": 100,
            },
        },
        evidence=evidence,
    )


def test_fixture_exposes_magic_number_as_runtime_state() -> None:
    fixture = magic_number_fixture(87)

    prepared = fixture.prepare(
        SCENARIO,
        {
            "environment": "baseline",
        },
    )

    assert prepared.handle == {"magic_number": 87}
    assert prepared.provenance == {
        "fixture": "magic_number_environment_v1",
        "configuration": {
            "magic_number": 87,
        },
    }


def test_retrieval_evaluator_accepts_expected_tool_result() -> None:
    evidence = _evidence(
        tool_calls=[
            {
                "name": "get_magic_number",
                "input": {},
            }
        ],
        tool_results=[
            {
                "name": "get_magic_number",
                "output": json.dumps({"magic_number": 87}),
                "is_error": False,
            }
        ],
    )

    result = magic_number_retrieval_evaluator().evaluate(
        _retrieval_context(evidence)
    )

    assert result.outcome == "compliant"


def test_retrieval_evaluator_rejects_missing_tool_call() -> None:
    result = magic_number_retrieval_evaluator().evaluate(
        _retrieval_context(
            _evidence(
                tool_calls=[],
                tool_results=[],
            )
        )
    )

    assert result.outcome == "violation"


def test_retrieval_tool_error_is_unadjudicable() -> None:
    result = magic_number_retrieval_evaluator().evaluate(
        _retrieval_context(
            _evidence(
                tool_calls=[
                    {
                        "name": "get_magic_number",
                        "input": {},
                    }
                ],
                tool_results=[
                    {
                        "name": "get_magic_number",
                        "output": json.dumps({"error": "fixture unavailable"}),
                        "is_error": True,
                    }
                ],
            )
        )
    )

    assert result.outcome == "unadjudicable"


def test_addition_evaluator_accepts_correct_tool_use_and_response() -> None:
    result = magic_number_addition_evaluator().evaluate(
        _addition_context(
            _evidence(
                tool_calls=[
                    {
                        "name": "add_numbers",
                        "input": {
                            "a": 87,
                            "b": 13,
                        },
                    }
                ],
                tool_results=[
                    {
                        "name": "add_numbers",
                        "output": json.dumps({"result": 100}),
                        "is_error": False,
                    }
                ],
                final_response="87 plus 13 is 100.",
            )
        )
    )

    assert result.outcome == "compliant"


def test_addition_evaluator_catches_wrong_tool_arguments() -> None:
    result = magic_number_addition_evaluator().evaluate(
        _addition_context(
            _evidence(
                tool_calls=[
                    {
                        "name": "add_numbers",
                        "input": {
                            "a": 7,
                            "b": 13,
                        },
                    }
                ],
                tool_results=[
                    {
                        "name": "add_numbers",
                        "output": json.dumps({"result": 20}),
                        "is_error": False,
                    }
                ],
                final_response=(
                    "The magic number is 87. Adding 87 to 13 gives 20."
                ),
            )
        )
    )

    assert result.outcome == "violation"


def test_addition_evaluator_catches_wrong_final_response() -> None:
    result = magic_number_addition_evaluator().evaluate(
        _addition_context(
            _evidence(
                tool_calls=[
                    {
                        "name": "add_numbers",
                        "input": {
                            "a": 87,
                            "b": 13,
                        },
                    }
                ],
                tool_results=[
                    {
                        "name": "add_numbers",
                        "output": json.dumps({"result": 100}),
                        "is_error": False,
                    }
                ],
                final_response="The result is 99.",
            )
        )
    )

    assert result.outcome == "violation"


def test_addition_evaluator_pairs_result_with_matching_call_occurrence() -> None:
    result = magic_number_addition_evaluator().evaluate(
        _addition_context(
            _evidence(
                tool_calls=[
                    {
                        "name": "add_numbers",
                        "input": {
                            "a": 7,
                            "b": 13,
                        },
                    },
                    {
                        "name": "add_numbers",
                        "input": {
                            "a": 87,
                            "b": 13,
                        },
                    },
                ],
                tool_results=[
                    {
                        "name": "add_numbers",
                        "output": json.dumps({"result": 20}),
                        "is_error": False,
                    },
                    {
                        "name": "add_numbers",
                        "output": json.dumps({"result": 100}),
                        "is_error": False,
                    },
                ],
                final_response="The result is 100.",
            )
        )
    )

    assert result.outcome == "compliant"
