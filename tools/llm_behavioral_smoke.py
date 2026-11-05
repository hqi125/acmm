from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from acmm.runtime.interfaces import adapter_execution_result
from acmm_infra.profiles.llm import (
    load_llm_baseline,
    primary_requested_context_window,
    runtime_provenance_configuration,
)


_PROMPT = (
    "Compute 87 + 13. Return the numeric result and briefly state the arithmetic. "
    "Do not use external tools."
)
_EXPECTED_RESULT = 100
_RESULT_PATTERN = re.compile(r"(?<![0-9])100(?![0-9])")


@dataclass(frozen=True)
class behavioral_smoke_assessment:
    passed: bool
    detail: str
    final_response: str | None


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
    return {}


def build_termination() -> dict[str, int]:
    return {"timeout_seconds": 120}


def assess_result(result: adapter_execution_result) -> behavioral_smoke_assessment:
    if result.failure is not None:
        return behavioral_smoke_assessment(
            passed=False,
            detail=f"{result.failure.kind}: {result.failure.message}",
            final_response=None,
        )

    if result.termination != "agent_completed":
        return behavioral_smoke_assessment(
            passed=False,
            detail=(
                "expected agent_completed termination, got "
                f"{result.termination!r}"
            ),
            final_response=None,
        )

    try:
        evidence = {item.id: item for item in result.evidence}
        final_response = evidence["final_response"].content.decode("utf-8")
    except (KeyError, UnicodeDecodeError) as error:
        return behavioral_smoke_assessment(
            passed=False,
            detail=f"could not read final_response evidence: {error}",
            final_response=None,
        )

    if _RESULT_PATTERN.search(final_response) is None:
        return behavioral_smoke_assessment(
            passed=False,
            detail=f"final response does not report expected result {_EXPECTED_RESULT}",
            final_response=final_response,
        )

    return behavioral_smoke_assessment(
        passed=True,
        detail="behavioral smoke result satisfied",
        final_response=final_response,
    )


def print_and_check_result(result: adapter_execution_result) -> int:
    print(f"termination: {result.termination}")
    assessment = assess_result(result)
    if not assessment.passed:
        print(f"smoke check failure: {assessment.detail}")
        print("smoke: FAIL")
        return 1

    assert assessment.final_response is not None
    print("final response:")
    print(assessment.final_response)
    print("checks:")
    print("  arithmetic result: PASS")
    print("  upstream model requests: observe externally; not a pass criterion")
    print("smoke: PASS")
    return 0
