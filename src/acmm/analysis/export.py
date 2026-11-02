from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable

import yaml

from acmm.contract.loader import contract_load_error, load_contract
from acmm.contract.validator import (
    default_contract_schema_path,
    load_schema,
    validate_contract,
)
from acmm.record.loader import load_run_record, run_record_load_error
from acmm.record.validator import (
    default_run_record_schema_path,
    load_run_record_schema,
    sha256_file,
    validate_run_record,
)
from acmm.runtime.planner import build_experiment_plan


EXPORT_SCHEMA_VERSION = "0.1"

RUN_COLUMNS = (
    "export_schema_version",
    "execution_id",
    "experiment_id",
    "contract_sha256",
    "harness_version",
    "execution_contract_run_count",
    "execution_selected_run_count",
    "execution_resolved_run_count",
    "max_attempts_per_run",
    "inter_run_delay_seconds",
    "run_index",
    "attempt",
    "selected",
    "run_id",
    "subject_id",
    "scenario_id",
    "repetition",
    "started_at",
    "ended_at",
    "termination",
    "validity",
    "failure_kind",
    "failure_message",
    "agent_name",
    "agent_version",
    "model_name",
    "model_provider",
    "subject_configuration_json",
    "environment_fixture",
    "environment_configuration_json",
    "evidence_count",
    "judgment_count",
    "result_path",
)

JUDGMENT_COLUMNS = (
    "export_schema_version",
    "execution_id",
    "experiment_id",
    "contract_sha256",
    "run_index",
    "attempt",
    "selected",
    "run_id",
    "subject_id",
    "scenario_id",
    "repetition",
    "validity",
    "termination",
    "opportunity_id",
    "outcome",
    "requirement_id",
    "dimension",
    "check_id",
    "evaluator",
    "evidence_ids_json",
    "detail",
)


class analysis_export_error(ValueError):
    """Raised when a completed execution cannot be exported safely."""


@dataclass(frozen=True)
class analysis_export_result:
    execution_id: str
    experiment_id: str
    output_dir: Path
    runs_path: Path
    judgments_path: Path
    manifest_path: Path
    run_rows: int
    judgment_rows: int


@dataclass(frozen=True)
class _validated_attempt:
    slot: dict[str, Any]
    attempt: dict[str, Any]
    record: dict[str, Any]
    result_path: str
    selected: bool


def export_execution(
    execution_dir: str | Path,
    output_root: str | Path,
    contract_schema_path: str | Path | None = None,
    run_record_schema_path: str | Path | None = None,
) -> analysis_export_result:
    source_dir = Path(execution_dir)
    manifest_path = source_dir / "experiment_execution.yaml"
    contract_path = source_dir / "contract.yaml"

    if not source_dir.is_dir():
        raise analysis_export_error(
            f"Experiment execution directory does not exist: '{source_dir}'."
        )

    manifest = _load_yaml_mapping(manifest_path, "execution manifest")
    contract = _load_and_validate_contract(
        contract_path,
        contract_schema_path,
    )
    record_schema = _load_record_schema(run_record_schema_path)

    execution = _validate_execution_header(
        manifest,
        contract,
        contract_path,
    )
    attempts = _load_validated_attempts(
        source_dir=source_dir,
        manifest=manifest,
        contract=contract,
        record_schema=record_schema,
        expected_contract_sha256=execution["contract_sha256"],
    )

    run_rows = [
        _build_run_row(execution, item)
        for item in attempts
    ]
    judgment_rows = list(
        _build_judgment_rows(
            execution,
            contract,
            attempts,
        )
    )

    runs_bytes = _csv_bytes(RUN_COLUMNS, run_rows)
    judgments_bytes = _csv_bytes(JUDGMENT_COLUMNS, judgment_rows)

    _validate_directory_component("experiment ID", execution["experiment_id"])
    _validate_directory_component("execution ID", execution["id"])

    output_dir = (
        Path(output_root)
        / execution["experiment_id"]
        / f"execution_{execution['id']}"
    )

    export_manifest = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "source": {
            "execution_id": execution["id"],
            "experiment_id": execution["experiment_id"],
            "contract_sha256": execution["contract_sha256"],
            "contract_snapshot": "contract.yaml",
            "execution_manifest": "experiment_execution.yaml",
            "execution_manifest_sha256": sha256_file(manifest_path),
        },
        "tables": {
            "runs": {
                "artifact": "runs.csv",
                "rows": len(run_rows),
                "sha256": _sha256_bytes(runs_bytes),
            },
            "judgments": {
                "artifact": "judgments.csv",
                "rows": len(judgment_rows),
                "sha256": _sha256_bytes(judgments_bytes),
            },
        },
    }
    manifest_bytes = yaml.safe_dump(
        export_manifest,
        sort_keys=False,
        allow_unicode=False,
    ).encode("utf-8")

    _publish_export(
        output_dir,
        {
            "runs.csv": runs_bytes,
            "judgments.csv": judgments_bytes,
            "export_manifest.yaml": manifest_bytes,
        },
    )

    return analysis_export_result(
        execution_id=execution["id"],
        experiment_id=execution["experiment_id"],
        output_dir=output_dir,
        runs_path=output_dir / "runs.csv",
        judgments_path=output_dir / "judgments.csv",
        manifest_path=output_dir / "export_manifest.yaml",
        run_rows=len(run_rows),
        judgment_rows=len(judgment_rows),
    )


def _load_and_validate_contract(
    contract_path: Path,
    contract_schema_path: str | Path | None,
) -> dict[str, Any]:
    schema_path = (
        Path(contract_schema_path)
        if contract_schema_path is not None
        else default_contract_schema_path()
    )

    try:
        contract = load_contract(contract_path)
        schema = load_schema(schema_path)
    except (contract_load_error, OSError, ValueError) as exc:
        raise analysis_export_error(str(exc)) from exc

    validation = validate_contract(contract, schema)

    if not validation.is_valid:
        issue = validation.errors[0]
        raise analysis_export_error(
            "Execution Contract snapshot is invalid: "
            f"{issue.path}: [{issue.code}] {issue.message}"
        )

    return contract


def _load_record_schema(
    run_record_schema_path: str | Path | None,
) -> dict[str, Any]:
    schema_path = (
        Path(run_record_schema_path)
        if run_record_schema_path is not None
        else default_run_record_schema_path()
    )

    try:
        return load_run_record_schema(schema_path)
    except (OSError, ValueError) as exc:
        raise analysis_export_error(
            f"Could not load Run Record schema '{schema_path}': {exc}"
        ) from exc


def _validate_execution_header(
    manifest: dict[str, Any],
    contract: dict[str, Any],
    contract_path: Path,
) -> dict[str, Any]:
    if manifest.get("schema_version") != "0.1":
        raise analysis_export_error(
            "Execution manifest schema_version must be '0.1'."
        )

    execution = manifest.get("execution")
    slots = manifest.get("slots")

    if not isinstance(execution, dict) or not isinstance(slots, list):
        raise analysis_export_error(
            "Execution manifest must contain an execution mapping and slots list."
        )

    required = (
        "id",
        "experiment_id",
        "contract_sha256",
        "harness_version",
        "max_attempts_per_run",
        "contract_run_count",
        "selected_run_indices",
        "planned_run_count",
        "resolved_run_count",
        "unresolved_run_indices",
    )

    for key in required:
        if key not in execution:
            raise analysis_export_error(
                f"Execution manifest is missing execution.{key}."
            )

    experiment_id = contract["experiment"]["id"]

    if execution["experiment_id"] != experiment_id:
        raise analysis_export_error(
            "Execution manifest experiment_id does not match its Contract snapshot."
        )

    actual_contract_sha256 = sha256_file(contract_path)

    if execution["contract_sha256"] != actual_contract_sha256:
        raise analysis_export_error(
            "Execution manifest Contract SHA-256 does not match contract.yaml bytes."
        )

    selected_indices = execution["selected_run_indices"]
    unresolved_indices = execution["unresolved_run_indices"]

    if not isinstance(selected_indices, list) or not all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in selected_indices
    ):
        raise analysis_export_error(
            "execution.selected_run_indices must be an integer list."
        )

    if not isinstance(unresolved_indices, list) or not all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in unresolved_indices
    ):
        raise analysis_export_error(
            "execution.unresolved_run_indices must be an integer list."
        )

    if execution["planned_run_count"] != len(slots):
        raise analysis_export_error(
            "Execution manifest planned_run_count does not match slots length."
        )

    plan = build_experiment_plan(contract)

    if execution["contract_run_count"] != plan.run_count:
        raise analysis_export_error(
            "Execution manifest contract_run_count does not match the Contract plan."
        )

    slot_indices = [
        slot.get("run_index")
        if isinstance(slot, dict)
        else None
        for slot in slots
    ]

    if selected_indices != slot_indices:
        raise analysis_export_error(
            "Execution manifest selected_run_indices does not match slot ordering."
        )

    if execution["planned_run_count"] != len(selected_indices):
        raise analysis_export_error(
            "Execution manifest selected_run_indices count does not match slots length."
        )

    if len(selected_indices) != len(set(selected_indices)):
        raise analysis_export_error(
            "execution.selected_run_indices must not contain duplicates."
        )

    max_attempts = execution["max_attempts_per_run"]

    if (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or max_attempts < 1
    ):
        raise analysis_export_error(
            "execution.max_attempts_per_run must be a positive integer."
        )

    pacing = execution.get("pacing", {})
    delay = pacing.get("inter_run_delay_seconds", 0) if isinstance(pacing, dict) else None

    if (
        not isinstance(delay, (int, float))
        or isinstance(delay, bool)
        or delay < 0
    ):
        raise analysis_export_error(
            "execution.pacing.inter_run_delay_seconds must be non-negative."
        )

    resolved_count = sum(
        1
        for slot in slots
        if isinstance(slot, dict) and slot.get("selected_run_id") is not None
    )

    if execution["resolved_run_count"] != resolved_count:
        raise analysis_export_error(
            "Execution manifest resolved_run_count does not match selected_run_id values."
        )

    actual_unresolved = [
        slot.get("run_index")
        for slot in slots
        if isinstance(slot, dict) and slot.get("selected_run_id") is None
    ]

    if unresolved_indices != actual_unresolved:
        raise analysis_export_error(
            "Execution manifest unresolved_run_indices does not match slot resolution."
        )

    return execution


def _load_validated_attempts(
    source_dir: Path,
    manifest: dict[str, Any],
    contract: dict[str, Any],
    record_schema: dict[str, Any],
    expected_contract_sha256: str,
) -> tuple[_validated_attempt, ...]:
    results: list[_validated_attempt] = []
    seen_run_ids: set[str] = set()
    seen_result_paths: set[str] = set()

    for slot_position, slot in enumerate(manifest["slots"]):
        if not isinstance(slot, dict):
            raise analysis_export_error(
                f"Execution manifest slots[{slot_position}] must be a mapping."
            )

        attempts = slot.get("attempts")
        selected_run_id = slot.get("selected_run_id")

        if not isinstance(attempts, list) or not attempts:
            raise analysis_export_error(
                f"Execution manifest slots[{slot_position}].attempts must be non-empty."
            )

        max_attempts = manifest["execution"]["max_attempts_per_run"]

        if len(attempts) > max_attempts:
            raise analysis_export_error(
                f"Execution manifest slots[{slot_position}] exceeds "
                "max_attempts_per_run."
            )

        slot_run_ids: list[str] = []

        for attempt_position, attempt in enumerate(attempts):
            if not isinstance(attempt, dict):
                raise analysis_export_error(
                    "Execution manifest attempt entries must be mappings."
                )

            result_path = attempt.get("result_path")
            run_id = attempt.get("run_id")

            if not isinstance(result_path, str) or not _is_safe_relative_path(result_path):
                raise analysis_export_error(
                    "Execution manifest contains an unsafe attempt result_path."
                )

            if not isinstance(run_id, str) or not run_id:
                raise analysis_export_error(
                    "Execution manifest attempt run_id must be a non-empty string."
                )

            if run_id in seen_run_ids:
                raise analysis_export_error(
                    f"Execution manifest contains duplicate run_id '{run_id}'."
                )

            if result_path in seen_result_paths:
                raise analysis_export_error(
                    f"Execution manifest contains duplicate result_path '{result_path}'."
                )

            seen_run_ids.add(run_id)
            seen_result_paths.add(result_path)
            slot_run_ids.append(run_id)

            record_path = source_dir / Path(result_path) / "run_record.yaml"

            try:
                record = load_run_record(record_path)
            except run_record_load_error as exc:
                raise analysis_export_error(str(exc)) from exc

            validation = validate_run_record(
                record,
                record_schema,
                contract,
                expected_contract_sha256=expected_contract_sha256,
            )

            if not validation.is_valid:
                issue = validation.errors[0]
                raise analysis_export_error(
                    f"Run Record '{record_path}' is invalid: "
                    f"{issue.path}: [{issue.code}] {issue.message}"
                )

            _validate_attempt_binding(
                slot=slot,
                attempt=attempt,
                record=record,
                slot_position=slot_position,
                attempt_position=attempt_position,
            )
            _validate_evidence_artifacts(
                source_dir / Path(result_path),
                record,
            )

            expected_attempt_number = attempt_position + 1

            if attempt["attempt"] != expected_attempt_number:
                raise analysis_export_error(
                    "Execution manifest attempt numbers must be contiguous from 1 "
                    f"within slots[{slot_position}]."
                )

            selected = selected_run_id == run_id

            if selected and record["execution"]["validity"] != "valid":
                raise analysis_export_error(
                    "Execution manifest selected_run_id points to an invalid run."
                )

            if not selected and record["execution"]["validity"] == "valid":
                raise analysis_export_error(
                    "Execution manifest contains a scientifically valid run that "
                    "was not selected for its slot."
                )

            results.append(
                _validated_attempt(
                    slot=slot,
                    attempt=attempt,
                    record=record,
                    result_path=result_path,
                    selected=selected,
                )
            )

        if selected_run_id is not None and selected_run_id not in slot_run_ids:
            raise analysis_export_error(
                "Execution manifest selected_run_id does not resolve within its slot."
            )

        if selected_run_id is not None and slot_run_ids[-1] != selected_run_id:
            raise analysis_export_error(
                "Execution manifest selected_run_id must identify the final attempt "
                "in its resolved slot."
            )

    return tuple(results)


def _validate_evidence_artifacts(
    run_dir: Path,
    record: dict[str, Any],
) -> None:
    for entry in record["evidence"]:
        artifact = entry["artifact"]

        if not _is_safe_relative_path(artifact):
            raise analysis_export_error(
                f"Run Record contains unsafe evidence artifact path '{artifact}'."
            )

        artifact_path = run_dir.joinpath(*artifact.split("/"))

        if not artifact_path.is_file():
            raise analysis_export_error(
                f"Run Record evidence artifact is missing: '{artifact_path}'."
            )

        actual_sha256 = sha256_file(artifact_path)

        if actual_sha256 != entry["sha256"]:
            raise analysis_export_error(
                f"Evidence artifact SHA-256 does not match Run Record: "
                f"'{artifact_path}'."
            )


def _validate_attempt_binding(
    slot: dict[str, Any],
    attempt: dict[str, Any],
    record: dict[str, Any],
    slot_position: int,
    attempt_position: int,
) -> None:
    run = record["run"]
    execution = record["execution"]
    expected = {
        "run_index": slot.get("run_index"),
        "subject_id": slot.get("subject_id"),
        "scenario_id": slot.get("scenario_id"),
        "repetition": slot.get("repetition"),
        "run_id": attempt.get("run_id"),
        "validity": attempt.get("validity"),
        "termination": attempt.get("termination"),
    }
    actual = {
        "run_index": run.get("run_index"),
        "subject_id": run.get("subject_id"),
        "scenario_id": run.get("scenario_id"),
        "repetition": run.get("repetition"),
        "run_id": run.get("id"),
        "validity": execution.get("validity"),
        "termination": execution.get("termination"),
    }

    if actual != expected:
        raise analysis_export_error(
            "Execution manifest attempt metadata does not match Run Record at "
            f"slots[{slot_position}].attempts[{attempt_position}]."
        )

    attempt_number = attempt.get("attempt")

    if (
        not isinstance(attempt_number, int)
        or isinstance(attempt_number, bool)
        or attempt_number < 1
    ):
        raise analysis_export_error(
            "Execution manifest attempt numbers must be positive integers."
        )


def _build_run_row(
    execution: dict[str, Any],
    item: _validated_attempt,
) -> dict[str, Any]:
    record = item.record
    run = record["run"]
    realized = record["execution"]
    provenance = record["provenance"]
    failure = realized.get("failure", {})
    subject = provenance.get("subject", {})
    agent = subject.get("agent", {})
    model = subject.get("model", {})
    environment = provenance.get("environment", {})
    pacing = execution.get("pacing", {})

    return {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "execution_id": execution["id"],
        "experiment_id": execution["experiment_id"],
        "contract_sha256": execution["contract_sha256"],
        "harness_version": execution["harness_version"],
        "execution_contract_run_count": execution["contract_run_count"],
        "execution_selected_run_count": execution["planned_run_count"],
        "execution_resolved_run_count": execution["resolved_run_count"],
        "max_attempts_per_run": execution["max_attempts_per_run"],
        "inter_run_delay_seconds": pacing.get("inter_run_delay_seconds", 0),
        "run_index": run["run_index"],
        "attempt": item.attempt["attempt"],
        "selected": item.selected,
        "run_id": run["id"],
        "subject_id": run["subject_id"],
        "scenario_id": run["scenario_id"],
        "repetition": run["repetition"],
        "started_at": realized["started_at"],
        "ended_at": realized["ended_at"],
        "termination": realized["termination"],
        "validity": realized["validity"],
        "failure_kind": failure.get("kind", ""),
        "failure_message": failure.get("message", ""),
        "agent_name": agent.get("name", ""),
        "agent_version": agent.get("version", ""),
        "model_name": model.get("name", ""),
        "model_provider": model.get("provider", ""),
        "subject_configuration_json": _json_cell(subject.get("configuration", {})),
        "environment_fixture": environment.get("fixture", ""),
        "environment_configuration_json": _json_cell(
            environment.get("configuration", {})
        ),
        "evidence_count": len(record["evidence"]),
        "judgment_count": len(record["judgments"]),
        "result_path": item.result_path,
    }


def _build_judgment_rows(
    execution: dict[str, Any],
    contract: dict[str, Any],
    attempts: tuple[_validated_attempt, ...],
) -> Iterable[dict[str, Any]]:
    scenarios = {
        scenario["id"]: scenario
        for scenario in contract["scenarios"]
    }
    requirements = {
        requirement["id"]: requirement
        for requirement in contract["requirements"]
    }
    checks = {
        check["id"]: check
        for check in contract["checks"]
    }

    for item in attempts:
        record = item.record
        run = record["run"]
        realized = record["execution"]
        scenario = scenarios[run["scenario_id"]]
        opportunities = {
            opportunity["id"]: opportunity
            for opportunity in scenario["opportunities"]
        }

        for judgment in record["judgments"]:
            opportunity = opportunities[judgment["opportunity_id"]]
            requirement = requirements[opportunity["requirement_id"]]
            check = checks[opportunity["check_id"]]

            yield {
                "export_schema_version": EXPORT_SCHEMA_VERSION,
                "execution_id": execution["id"],
                "experiment_id": execution["experiment_id"],
                "contract_sha256": execution["contract_sha256"],
                "run_index": run["run_index"],
                "attempt": item.attempt["attempt"],
                "selected": item.selected,
                "run_id": run["id"],
                "subject_id": run["subject_id"],
                "scenario_id": run["scenario_id"],
                "repetition": run["repetition"],
                "validity": realized["validity"],
                "termination": realized["termination"],
                "opportunity_id": judgment["opportunity_id"],
                "outcome": judgment["outcome"],
                "requirement_id": opportunity["requirement_id"],
                "dimension": requirement["dimension"],
                "check_id": opportunity["check_id"],
                "evaluator": check["evaluator"],
                "evidence_ids_json": _json_cell(judgment["evidence_ids"]),
                "detail": judgment.get("detail", ""),
            }


def _csv_bytes(
    columns: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=columns,
        extrasaction="raise",
        lineterminator="\n",
    )
    writer.writeheader()

    for row in rows:
        writer.writerow(
            {
                key: _csv_cell(row.get(key, ""))
                for key in columns
            }
        )

    return stream.getvalue().encode("utf-8")


def _csv_cell(value: Any) -> str | int | float:
    if value is None:
        return ""

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, (str, int, float)):
        return value

    raise analysis_export_error(
        f"Unsupported CSV cell value type: {type(value).__name__}."
    )


def _json_cell(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _publish_export(
    output_dir: Path,
    files: dict[str, bytes],
) -> None:
    if output_dir.exists():
        raise FileExistsError(
            f"Analysis export directory already exists: '{output_dir}'."
        )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.tmp-",
            dir=output_dir.parent,
        )
    )

    try:
        for name, content in files.items():
            path = temp_dir / name

            with path.open("xb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())

        os.replace(temp_dir, output_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as exc:
        raise analysis_export_error(
            f"Could not read {label} '{path}': {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise analysis_export_error(
            f"Could not parse {label} '{path}' as YAML: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise analysis_export_error(
            f"{label.capitalize()} '{path}' must contain a YAML mapping."
        )

    return data


def _is_safe_relative_path(value: str) -> bool:
    if not value or "\\" in value or value.startswith("/"):
        return False

    parts = value.split("/")

    if any(part in {"", ".", ".."} for part in parts):
        return False

    if ":" in parts[0]:
        return False

    return True


def _validate_directory_component(
    label: str,
    value: object,
) -> None:
    if not isinstance(value, str) or not value:
        raise analysis_export_error(
            f"{label.capitalize()} must be a non-empty string."
        )

    if value in {".", ".."} or "/" in value or "\\" in value or ":" in value:
        raise analysis_export_error(
            f"{label.capitalize()} is not safe for an export-directory name: "
            f"'{value}'."
        )


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
