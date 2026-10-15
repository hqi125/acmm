from __future__ import annotations

from typing import Any, Iterable

from acmm.validation import validation_issue, validation_result
from acmm.harness.capabilities import harness_capabilities


def validate_harness_compatibility(
    contract: dict[str, Any],
    capabilities: harness_capabilities,
) -> validation_result:
    issues = tuple(_iter_compatibility_issues(contract, capabilities))

    return validation_result(
        errors=tuple(issue for issue in issues if issue.severity == "error"),
        warnings=tuple(issue for issue in issues if issue.severity == "warning"),
    )


def _iter_compatibility_issues(
    contract: dict[str, Any],
    capabilities: harness_capabilities,
) -> Iterable[validation_issue]:
    for check_index, check in enumerate(contract["checks"]):
        evaluator = check["evaluator"]

        if evaluator not in capabilities.evaluators:
            yield validation_issue(
                severity="error",
                code="unavailable_evaluator",
                path=f"checks[{check_index}].evaluator",
                message=f"Evaluator '{evaluator}' is not registered by the harness.",
            )

    for scenario_index, scenario in enumerate(contract["scenarios"]):
        environment = scenario.get("environment", {})
        fixture = environment.get("fixture")

        if fixture is not None and fixture not in capabilities.fixtures:
            yield validation_issue(
                severity="error",
                code="unavailable_fixture",
                path=f"scenarios[{scenario_index}].environment.fixture",
                message=f"Fixture '{fixture}' is not registered by the harness.",
            )

        tools = scenario.get("capabilities", {}).get("tools", [])

        for tool_index, tool in enumerate(tools):
            if tool not in capabilities.tools:
                yield validation_issue(
                    severity="error",
                    code="unavailable_tool",
                    path=(
                        f"scenarios[{scenario_index}].capabilities.tools[{tool_index}]"
                    ),
                    message=f"Tool '{tool}' is not available from the harness.",
                )

    capture = contract["evidence_policy"]["capture"]

    for evidence_index, evidence_channel in enumerate(capture):
        if evidence_channel not in capabilities.evidence_channels:
            yield validation_issue(
                severity="error",
                code="unsupported_evidence_channel",
                path=f"evidence_policy.capture[{evidence_index}]",
                message=(
                    f"Evidence channel '{evidence_channel}' cannot be captured "
                    "by the harness."
                ),
            )
