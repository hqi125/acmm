from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator


from acmm.validation import validation_issue, validation_result, split_issues

def load_schema(path: str | Path) -> dict[str, Any]:
    schema_path = Path(path)

    with schema_path.open("r", encoding="utf-8") as file:
        schema = json.load(file)

    Draft202012Validator.check_schema(schema)
    return schema


def validate_contract(
    contract: dict[str, Any],
    schema: dict[str, Any],
) -> validation_result:
    issues: list[validation_issue] = []

    issues.extend(_validate_schema(contract, schema))

    if any(issue.severity == "error" for issue in issues):
        return split_issues(issues)

    issues.extend(_validate_semantics(contract))
    return split_issues(issues)


def _validate_schema(
    contract: dict[str, Any],
    schema: dict[str, Any],
) -> Iterable[validation_issue]:
    validator = Draft202012Validator(schema)

    errors = sorted(
        validator.iter_errors(contract),
        key=lambda error: list(error.absolute_path),
    )

    for error in errors:
        yield validation_issue(
            severity="error",
            code="schema_violation",
            path=_format_path(error.absolute_path),
            message=error.message,
        )


def _validate_semantics(
    contract: dict[str, Any],
) -> Iterable[validation_issue]:
    subjects = contract["subjects"]
    requirements = contract["requirements"]
    checks = contract["checks"]
    scenarios = contract["scenarios"]

    yield from _check_unique_ids("subjects", subjects)
    yield from _check_unique_ids("requirements", requirements)
    yield from _check_unique_ids("checks", checks)
    yield from _check_unique_ids("scenarios", scenarios)

    requirement_ids = {item["id"] for item in requirements}
    check_ids = {item["id"] for item in checks}

    used_requirement_ids: set[str] = set()
    used_check_ids: set[str] = set()

    for scenario_index, scenario in enumerate(scenarios):
        opportunities = scenario["opportunities"]

        yield from _check_unique_ids(
            f"scenarios[{scenario_index}].opportunities",
            opportunities,
        )

        for opportunity_index, opportunity in enumerate(opportunities):
            opportunity_path = (
                f"scenarios[{scenario_index}].opportunities[{opportunity_index}]"
            )

            requirement_id = opportunity["requirement_id"]
            check_id = opportunity["check_id"]

            if requirement_id not in requirement_ids:
                yield validation_issue(
                    severity="error",
                    code="unresolved_requirement_reference",
                    path=f"{opportunity_path}.requirement_id",
                    message=(
                        f"Requirement '{requirement_id}' is not declared in "
                        "'requirements'."
                    ),
                )
            else:
                used_requirement_ids.add(requirement_id)

            if check_id not in check_ids:
                yield validation_issue(
                    severity="error",
                    code="unresolved_check_reference",
                    path=f"{opportunity_path}.check_id",
                    message=f"Check '{check_id}' is not declared in 'checks'.",
                )
            else:
                used_check_ids.add(check_id)

    for requirement_id in sorted(requirement_ids - used_requirement_ids):
        yield validation_issue(
            severity="warning",
            code="unused_requirement",
            path=_find_id_path("requirements", requirements, requirement_id),
            message=f"Requirement '{requirement_id}' is never referenced.",
        )

    for check_id in sorted(check_ids - used_check_ids):
        yield validation_issue(
            severity="warning",
            code="unused_check",
            path=_find_id_path("checks", checks, check_id),
            message=f"Check '{check_id}' is never referenced.",
        )


def _check_unique_ids(
    collection_path: str,
    items: list[dict[str, Any]],
) -> Iterable[validation_issue]:
    first_index: dict[str, int] = {}

    for index, item in enumerate(items):
        item_id = item["id"]

        if item_id in first_index:
            yield validation_issue(
                severity="error",
                code="duplicate_id",
                path=f"{collection_path}[{index}].id",
                message=(
                    f"ID '{item_id}' duplicates "
                    f"{collection_path}[{first_index[item_id]}].id."
                ),
            )
        else:
            first_index[item_id] = index


def _find_id_path(
    collection_path: str,
    items: list[dict[str, Any]],
    item_id: str,
) -> str:
    for index, item in enumerate(items):
        if item["id"] == item_id:
            return f"{collection_path}[{index}].id"

    raise AssertionError(f"ID '{item_id}' was not found in '{collection_path}'.")


def _format_path(path: Iterable[Any]) -> str:
    components: list[str] = []

    for part in path:
        if isinstance(part, int):
            if components:
                components[-1] += f"[{part}]"
            else:
                components.append(f"[{part}]")
        else:
            components.append(str(part))

    return ".".join(components) if components else "$"
