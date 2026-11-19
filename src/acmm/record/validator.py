from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from acmm.runtime.planner import build_experiment_plan
from acmm.validation import validation_issue, validation_result, split_issues


def default_run_record_schema_path() -> Path:
    """Return the repository Run Record v0.1 schema used by ACMM Core."""

    return (
        Path(__file__).resolve().parents[3]
        / "records"
        / "v0_1"
        / "run_record.schema.json"
    )


def load_run_record_schema(path: str | Path) -> dict[str, Any]:
    schema_path = Path(path)

    with schema_path.open("r", encoding="utf-8") as file:
        schema = json.load(file)

    Draft202012Validator.check_schema(schema)
    return schema


def sha256_file(path: str | Path) -> str:
    source_path = Path(path)
    digest = hashlib.sha256()

    with source_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def validate_run_record(
    record: dict[str, Any],
    schema: dict[str, Any],
    contract: dict[str, Any],
    expected_contract_sha256: str | None = None,
) -> validation_result:
    issues: list[validation_issue] = []

    issues.extend(_validate_schema(record, schema))

    if any(issue.severity == "error" for issue in issues):
        return split_issues(issues)

    issues.extend(
        _validate_semantics(
            record,
            contract,
            expected_contract_sha256,
        )
    )

    return split_issues(issues)


def _validate_schema(
    record: dict[str, Any],
    schema: dict[str, Any],
) -> Iterable[validation_issue]:
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )

    errors = sorted(
        validator.iter_errors(record),
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
    record: dict[str, Any],
    contract: dict[str, Any],
    expected_contract_sha256: str | None,
) -> Iterable[validation_issue]:
    run = record["run"]
    execution = record["execution"]
    evidence = record["evidence"]
    judgments = record["judgments"]

    if run["experiment_id"] != contract["experiment"]["id"]:
        yield validation_issue(
            severity="error",
            code="experiment_mismatch",
            path="run.experiment_id",
            message=(
                f"Run Record experiment '{run['experiment_id']}' does not match "
                f"Contract experiment '{contract['experiment']['id']}'."
            ),
        )

    if (
        expected_contract_sha256 is not None
        and run["contract_sha256"] != expected_contract_sha256
    ):
        yield validation_issue(
            severity="error",
            code="contract_hash_mismatch",
            path="run.contract_sha256",
            message=(
                "Run Record Contract SHA-256 does not match the supplied "
                "Experiment Contract artifact."
            ),
        )

    subjects = {subject["id"] for subject in contract["subjects"]}
    scenarios = {
        scenario["id"]: scenario
        for scenario in contract["scenarios"]
    }

    if run["subject_id"] not in subjects:
        yield validation_issue(
            severity="error",
            code="unresolved_subject_reference",
            path="run.subject_id",
            message=(
                f"Subject '{run['subject_id']}' is not declared in the Contract."
            ),
        )

    scenario = scenarios.get(run["scenario_id"])

    if scenario is None:
        yield validation_issue(
            severity="error",
            code="unresolved_scenario_reference",
            path="run.scenario_id",
            message=(
                f"Scenario '{run['scenario_id']}' is not declared in the Contract."
            ),
        )

    plan = build_experiment_plan(contract)

    if run["run_index"] >= plan.run_count:
        yield validation_issue(
            severity="error",
            code="run_index_out_of_range",
            path="run.run_index",
            message=(
                f"Run index {run['run_index']} is outside the planned range "
                f"0..{plan.run_count - 1}."
            ),
        )
    else:
        planned = plan.runs[run["run_index"]]
        actual_coordinate = (
            run["subject_id"],
            run["scenario_id"],
            run["repetition"],
        )
        planned_coordinate = (
            planned.subject_id,
            planned.scenario_id,
            planned.repetition,
        )

        if actual_coordinate != planned_coordinate:
            yield validation_issue(
                severity="error",
                code="planned_run_mismatch",
                path="run",
                message=(
                    "Run coordinates do not match the planned run at "
                    f"index {run['run_index']}."
                ),
            )

    started_at = _parse_datetime(execution["started_at"])
    ended_at = _parse_datetime(execution["ended_at"])

    if ended_at < started_at:
        yield validation_issue(
            severity="error",
            code="invalid_execution_time_order",
            path="execution.ended_at",
            message="Execution end time precedes execution start time.",
        )

    evidence_ids: dict[str, int] = {}
    evidence_channels: dict[str, int] = {}
    evidence_artifacts: dict[str, int] = {}

    for index, entry in enumerate(evidence):
        evidence_id = entry["id"]
        channel = entry["channel"]
        artifact = entry["artifact"]

        if evidence_id in evidence_ids:
            yield validation_issue(
                severity="error",
                code="duplicate_evidence_id",
                path=f"evidence[{index}].id",
                message=(
                    f"Evidence ID '{evidence_id}' duplicates "
                    f"evidence[{evidence_ids[evidence_id]}].id."
                ),
            )
        else:
            evidence_ids[evidence_id] = index

        if channel in evidence_channels:
            yield validation_issue(
                severity="error",
                code="duplicate_evidence_channel",
                path=f"evidence[{index}].channel",
                message=(
                    f"Evidence channel '{channel}' duplicates "
                    f"evidence[{evidence_channels[channel]}].channel."
                ),
            )
        else:
            evidence_channels[channel] = index

        if artifact in evidence_artifacts:
            yield validation_issue(
                severity="error",
                code="duplicate_artifact_path",
                path=f"evidence[{index}].artifact",
                message=(
                    f"Evidence artifact path '{artifact}' duplicates "
                    f"evidence[{evidence_artifacts[artifact]}].artifact."
                ),
            )
        else:
            evidence_artifacts[artifact] = index

        if not _is_safe_artifact_path(artifact):
            yield validation_issue(
                severity="error",
                code="unsafe_artifact_path",
                path=f"evidence[{index}].artifact",
                message=(
                    "Evidence artifact path must be a relative POSIX-style path "
                    "without '.', '..', empty segments, backslashes, or drive prefixes."
                ),
            )

    diagnostics = record.get("diagnostics", [])
    diagnostic_ids: dict[str, int] = {}
    diagnostic_artifacts: dict[str, int] = {}
    for index, entry in enumerate(diagnostics):
        diagnostic_id = entry["id"]
        artifact = entry["artifact"]

        if diagnostic_id in evidence_ids:
            yield validation_issue(
                severity="error",
                code="duplicate_artifact_id",
                path=f"diagnostics[{index}].id",
                message=(
                    f"Diagnostic ID '{diagnostic_id}' duplicates an evidence ID."
                ),
            )
        elif diagnostic_id in diagnostic_ids:
            yield validation_issue(
                severity="error",
                code="duplicate_diagnostic_id",
                path=f"diagnostics[{index}].id",
                message=(
                    f"Diagnostic ID '{diagnostic_id}' duplicates "
                    f"diagnostics[{diagnostic_ids[diagnostic_id]}].id."
                ),
            )
        else:
            diagnostic_ids[diagnostic_id] = index

        if artifact in evidence_artifacts:
            yield validation_issue(
                severity="error",
                code="duplicate_artifact_path",
                path=f"diagnostics[{index}].artifact",
                message=(
                    f"Diagnostic artifact path '{artifact}' duplicates an evidence artifact path."
                ),
            )
        elif artifact in diagnostic_artifacts:
            yield validation_issue(
                severity="error",
                code="duplicate_artifact_path",
                path=f"diagnostics[{index}].artifact",
                message=(
                    f"Diagnostic artifact path '{artifact}' duplicates "
                    f"diagnostics[{diagnostic_artifacts[artifact]}].artifact."
                ),
            )
        else:
            diagnostic_artifacts[artifact] = index

        if not _is_safe_artifact_path(artifact):
            yield validation_issue(
                severity="error",
                code="unsafe_artifact_path",
                path=f"diagnostics[{index}].artifact",
                message=(
                    "Diagnostic artifact path must be a relative POSIX-style path "
                    "without '.', '..', empty segments, backslashes, or drive prefixes."
                ),
            )

    expected_channels = set(contract["evidence_policy"]["capture"])
    actual_channels = set(evidence_channels)

    for channel in sorted(actual_channels - expected_channels):
        index = evidence_channels[channel]
        yield validation_issue(
            severity="error",
            code="undeclared_evidence_channel",
            path=f"evidence[{index}].channel",
            message=(
                f"Evidence channel '{channel}' was not requested by the Contract."
            ),
        )

    if execution["validity"] == "valid":
        for channel in sorted(expected_channels - actual_channels):
            yield validation_issue(
                severity="error",
                code="missing_evidence_channel",
                path="evidence",
                message=(
                    f"Valid run is missing Contract-required evidence channel "
                    f"'{channel}'."
                ),
            )

    opportunity_ids: set[str] = set()

    if scenario is not None:
        opportunity_ids = {
            opportunity["id"]
            for opportunity in scenario["opportunities"]
        }

    judgment_ids: dict[str, int] = {}

    for index, judgment in enumerate(judgments):
        opportunity_id = judgment["opportunity_id"]

        if opportunity_id in judgment_ids:
            yield validation_issue(
                severity="error",
                code="duplicate_opportunity_judgment",
                path=f"judgments[{index}].opportunity_id",
                message=(
                    f"Opportunity '{opportunity_id}' already has a judgment at "
                    f"judgments[{judgment_ids[opportunity_id]}]."
                ),
            )
        else:
            judgment_ids[opportunity_id] = index

        if scenario is not None and opportunity_id not in opportunity_ids:
            yield validation_issue(
                severity="error",
                code="unresolved_opportunity_reference",
                path=f"judgments[{index}].opportunity_id",
                message=(
                    f"Opportunity '{opportunity_id}' is not declared by scenario "
                    f"'{run['scenario_id']}'."
                ),
            )

        for evidence_index, evidence_id in enumerate(judgment["evidence_ids"]):
            if evidence_id not in evidence_ids:
                yield validation_issue(
                    severity="error",
                    code="unresolved_evidence_reference",
                    path=(
                        f"judgments[{index}].evidence_ids[{evidence_index}]"
                    ),
                    message=(
                        f"Evidence '{evidence_id}' is not declared in the "
                        "Run Record evidence manifest."
                    ),
                )

    if execution["validity"] == "valid" and scenario is not None:
        actual_judgment_ids = set(judgment_ids)

        for opportunity_id in sorted(opportunity_ids - actual_judgment_ids):
            yield validation_issue(
                severity="error",
                code="missing_opportunity_judgment",
                path="judgments",
                message=(
                    f"Valid run is missing a judgment for opportunity "
                    f"'{opportunity_id}'."
                ),
            )


def _parse_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)


def _is_safe_artifact_path(value: str) -> bool:
    if "\\" in value:
        return False

    if value.startswith("/"):
        return False

    parts = value.split("/")

    if any(part in {"", ".", ".."} for part in parts):
        return False

    if ":" in parts[0]:
        return False

    return True


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
