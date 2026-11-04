from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator


from acmm.validation import validation_issue, validation_result, split_issues


_CONTRACT_SCHEMA_DIRECTORIES = {
    "0.1": "v0_1",
    "0.2": "v0_2",
}


def contract_schema_path(schema_version: str) -> Path:
    """Return the bundled schema path for one supported Contract version."""

    directory = _CONTRACT_SCHEMA_DIRECTORIES.get(schema_version)
    if directory is None:
        raise ValueError(f"Unsupported Experiment Contract schema_version '{schema_version}'.")
    return (
        Path(__file__).resolve().parents[3]
        / "contracts"
        / directory
        / "contract.schema.json"
    )


def default_contract_schema_path() -> Path:
    """Return the historical Contract v0.1 schema path for compatibility callers."""

    return contract_schema_path("0.1")


def load_schema_for_contract(
    contract: Mapping[str, Any],
    schema_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load an explicit schema override or the bundled schema selected by the Contract."""

    if schema_path is not None:
        return load_schema(schema_path)

    schema_version = contract.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        raise ValueError("Experiment Contract requires a non-empty string schema_version.")
    return load_schema(contract_schema_path(schema_version))

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

    protocol = contract["run_protocol"]
    ordering = protocol.get("ordering", "subject_scenario_repetition")
    scenario_ordering = protocol.get("scenario_ordering", "declaration")

    if (
        scenario_ordering == "cyclic_rotation"
        and ordering != "subject_repetition_scenario"
    ):
        yield validation_issue(
            severity="error",
            code="incompatible_run_ordering",
            path="run_protocol.scenario_ordering",
            message=(
                "scenario_ordering='cyclic_rotation' requires "
                "ordering='subject_repetition_scenario'."
            ),
        )

    for scenario_index, scenario in enumerate(scenarios):
        opportunities = scenario["opportunities"]

        yield from _check_unique_ids(
            f"scenarios[{scenario_index}].opportunities",
            opportunities,
        )

        yield from _validate_output_semantics(
            scenario,
            scenario_index,
            set(contract["evidence_policy"]["capture"]),
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


def _validate_output_semantics(
    scenario: dict[str, Any],
    scenario_index: int,
    captured_channels: set[str],
) -> Iterable[validation_issue]:
    task = scenario.get("task")
    if not isinstance(task, dict):
        return
    output_semantics = task.get("output_semantics")
    if not isinstance(output_semantics, dict):
        return
    structured = output_semantics.get("structured_object")
    payload_path: str | None = None
    if isinstance(structured, dict):
        evidence_id = structured.get("evidence_id")
        payload_path = structured.get("payload_path")
        structured_path = (
            f"scenarios[{scenario_index}].task.output_semantics.structured_object"
        )
        if isinstance(evidence_id, str) and evidence_id not in captured_channels:
            yield validation_issue(
                severity="error",
                code="uncaptured_interpreted_evidence",
                path=f"{structured_path}.evidence_id",
                message=(
                    f"Structured output interpretation requires evidence '{evidence_id}', "
                    "but evidence_policy.capture does not request that channel."
                ),
            )

    fields = output_semantics.get("scalar_fields")
    if not isinstance(fields, list):
        return

    seen_paths: dict[str, int] = {}
    for field_index, field in enumerate(fields):
        if not isinstance(field, dict):
            continue
        path = field.get("path")
        field_path = (
            f"scenarios[{scenario_index}].task.output_semantics."
            f"scalar_fields[{field_index}]"
        )
        if isinstance(path, str):
            if isinstance(payload_path, str) and not (
                path == payload_path or path.startswith(payload_path + ".")
            ):
                yield validation_issue(
                    severity="error",
                    code="output_semantic_path_outside_payload",
                    path=f"{field_path}.path",
                    message=(
                        f"Output semantic path '{path}' is outside the declared "
                        f"structured payload path '{payload_path}'."
                    ),
                )
            if path in seen_paths:
                yield validation_issue(
                    severity="error",
                    code="duplicate_output_semantic_path",
                    path=f"{field_path}.path",
                    message=(
                        f"Output semantic path '{path}' duplicates scalar_fields"
                        f"[{seen_paths[path]}].path."
                    ),
                )
            else:
                seen_paths[path] = field_index

        semantic_type = field.get("semantic_type")
        serializations = field.get("accepted_serializations")
        if (
            semantic_type == "string"
            and isinstance(serializations, list)
            and "canonical_string" in serializations
        ):
            yield validation_issue(
                severity="error",
                code="redundant_string_serialization",
                path=f"{field_path}.accepted_serializations",
                message=(
                    "semantic_type='string' is already represented natively as a JSON string; "
                    "canonical_string is not a distinct serialization."
                ),
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
