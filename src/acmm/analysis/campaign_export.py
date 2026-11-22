from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import yaml

from acmm.analysis.export import (
    analysis_export_error,
    _csv_bytes,
    _json_cell,
    _load_and_validate_contract,
    _load_record_schema,
    _publish_export,
    _sha256_bytes,
    _validate_directory_component,
)
from acmm.campaign.adjudication import validate_campaign_adjudication_manifest
from acmm.campaign.execution import (
    read_campaign_events,
    validate_campaign_execution_manifest,
)
from acmm.campaign.merge import validate_campaign_merge_manifest
from acmm.campaign.model import validate_campaign_manifest
from acmm.record.loader import load_run_record, run_record_load_error
from acmm.record.validator import sha256_file, validate_run_record


CAMPAIGN_EXPORT_SCHEMA_VERSION = "0.3"

PLANNED_RUN_COLUMNS = (
    "export_schema_version",
    "campaign_id",
    "experiment_id",
    "design_digest",
    "contract_sha256",
    "package_sha256",
    "run_index",
    "logical_run_id",
    "subject_id",
    "scenario_id",
    "repetition",
    "materialization_key",
    "observed",
    "accepted_run_id",
    "source_execution_id",
    "source_execution_status",
    "source_attempt",
)

RUN_COLUMNS = (
    "export_schema_version",
    "campaign_id",
    "experiment_id",
    "design_digest",
    "contract_sha256",
    "package_sha256",
    "source_execution_id",
    "source_execution_status",
    "source_execution_order",
    "source_attempt",
    "attempt",
    "source_selected",
    "selected",
    "logical_run_id",
    "run_index",
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
    "judgment_source",
    "source_result_path",
    "authoritative_result_path",
)

TOOL_CALL_COLUMNS = (
    "export_schema_version",
    "campaign_id",
    "experiment_id",
    "design_digest",
    "contract_sha256",
    "package_sha256",
    "source_execution_id",
    "source_execution_order",
    "source_attempt",
    "attempt",
    "source_selected",
    "selected",
    "logical_run_id",
    "run_index",
    "run_id",
    "subject_id",
    "scenario_id",
    "repetition",
    "validity",
    "termination",
    "call_index",
    "call_id",
    "tool_name",
    "transport_name",
    "input_json",
    "timestamp",
)

TOOL_RESULT_COLUMNS = (
    "export_schema_version",
    "campaign_id",
    "experiment_id",
    "design_digest",
    "contract_sha256",
    "package_sha256",
    "source_execution_id",
    "source_execution_order",
    "source_attempt",
    "attempt",
    "source_selected",
    "selected",
    "logical_run_id",
    "run_index",
    "run_id",
    "subject_id",
    "scenario_id",
    "repetition",
    "validity",
    "termination",
    "result_index",
    "tool_call_id",
    "tool_name",
    "transport_name",
    "output",
    "is_error",
    "timestamp",
)

JUDGMENT_COLUMNS = (
    "export_schema_version",
    "campaign_id",
    "experiment_id",
    "design_digest",
    "contract_sha256",
    "package_sha256",
    "source_execution_id",
    "source_execution_order",
    "source_attempt",
    "attempt",
    "source_selected",
    "selected",
    "logical_run_id",
    "run_index",
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
    "judgment_source",
)


@dataclass(frozen=True)
class campaign_analysis_export_result:
    campaign_id: str
    merge_id: str
    experiment_id: str
    output_dir: Path
    planned_runs_path: Path
    runs_path: Path
    judgments_path: Path
    tool_calls_path: Path
    tool_results_path: Path
    manifest_path: Path
    planned_run_rows: int
    run_rows: int
    judgment_rows: int
    tool_call_rows: int
    tool_result_rows: int


@dataclass
class _campaign_attempt:
    logical_run_id: str
    run_index: int
    source_execution_id: str
    source_execution_status: str
    source_execution_order: int
    source_attempt: int
    source_selected: bool
    source_result_path: str
    source_result_dir: Path
    record: dict[str, Any]
    attempt: int = 0
    selected: bool = False
    authoritative_result_path: str = ""
    authoritative_record: dict[str, Any] | None = None


def _report_progress(
    progress: Callable[[str], None] | None,
    message: str,
) -> None:
    if progress is not None:
        progress(message)


def export_campaign(
    campaign_dir: str | Path,
    merge_dir: str | Path,
    output_root: str | Path,
    contract_schema_path: str | Path | None = None,
    run_record_schema_path: str | Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> campaign_analysis_export_result:
    _report_progress(progress, "loading and validating Campaign and Merge metadata...")
    campaign_path = Path(campaign_dir)
    merge_path = Path(merge_dir)
    if not campaign_path.is_dir():
        raise analysis_export_error(
            f"Campaign directory does not exist: '{campaign_path}'."
        )
    if not merge_path.is_dir():
        raise analysis_export_error(
            f"Campaign merge directory does not exist: '{merge_path}'."
        )

    campaign_manifest_path = campaign_path / "campaign.json"
    merge_manifest_path = merge_path / "campaign_merge.json"
    campaign = _load_json_mapping(campaign_manifest_path, "campaign manifest")
    merge = _load_json_mapping(merge_manifest_path, "campaign merge manifest")
    try:
        validate_campaign_manifest(campaign)
        validate_campaign_merge_manifest(merge)
    except ValueError as exc:
        raise analysis_export_error(str(exc)) from exc

    identity = _validate_campaign_merge_binding(
        campaign_path=campaign_path,
        campaign_manifest_path=campaign_manifest_path,
        campaign=campaign,
        merge_path=merge_path,
        merge_manifest_path=merge_manifest_path,
        merge=merge,
    )

    _report_progress(progress, "validating frozen Contract snapshots...")
    contract_path = campaign_path / campaign["campaign"]["contract_artifact"]
    merge_contract_path = merge_path / "contract.yaml"
    contract = _load_and_validate_contract(contract_path, contract_schema_path)
    record_schema = _load_record_schema(run_record_schema_path)
    expected_contract_sha256 = identity["contract_sha256"]
    if sha256_file(contract_path) != expected_contract_sha256:
        raise analysis_export_error(
            "Campaign Contract SHA-256 does not match campaign provenance."
        )
    if sha256_file(merge_contract_path) != expected_contract_sha256:
        raise analysis_export_error(
            "Merged Contract SHA-256 does not match campaign provenance."
        )
    if contract_path.read_bytes() != merge_contract_path.read_bytes():
        raise analysis_export_error(
            "Campaign and merge Contract snapshots are not byte-identical."
        )

    planned_by_id, planned_by_index = _planned_runs(campaign)
    _report_progress(
        progress,
        f"loading and validating {merge['population']['observed']} merged observation(s)...",
    )
    accepted, merged_records = _load_accepted_observations(
        campaign_path=campaign_path,
        merge_path=merge_path,
        merge=merge,
        planned_by_id=planned_by_id,
        contract=contract,
        record_schema=record_schema,
        expected_contract_sha256=expected_contract_sha256,
    )
    _report_progress(progress, "loading Campaign execution and retry history...")
    execution_provenance, attempts = _load_campaign_attempt_history(
        campaign_path=campaign_path,
        campaign=campaign,
        planned_by_index=planned_by_index,
        contract=contract,
        record_schema=record_schema,
        expected_contract_sha256=expected_contract_sha256,
    )
    _report_progress(
        progress,
        (
            f"reconciling {len(attempts)} attempt(s) across "
            f"{len(execution_provenance)} Campaign Execution(s) with "
            f"{len(accepted)} accepted observation(s)..."
        ),
    )
    _bind_attempts_to_merge(attempts, accepted, merged_records)
    _assign_campaign_attempt_ordinals(attempts)

    _report_progress(progress, "building deterministic analysis tables...")
    planned_rows = _build_planned_rows(identity, campaign, accepted)
    run_rows = [_build_run_row(identity, item) for item in _ordered_attempts(attempts)]
    ordered_attempts = _ordered_attempts(attempts)
    judgment_rows = list(
        _build_judgment_rows(
            identity,
            contract,
            ordered_attempts,
        )
    )
    _report_progress(progress, "normalizing tool-call and tool-result trajectory evidence...")
    tool_call_rows = list(_build_tool_call_rows(identity, ordered_attempts))
    tool_result_rows = list(_build_tool_result_rows(identity, ordered_attempts))

    planned_bytes = _csv_bytes(PLANNED_RUN_COLUMNS, planned_rows)
    runs_bytes = _csv_bytes(RUN_COLUMNS, run_rows)
    judgments_bytes = _csv_bytes(JUDGMENT_COLUMNS, judgment_rows)
    tool_calls_bytes = _csv_bytes(TOOL_CALL_COLUMNS, tool_call_rows)
    tool_results_bytes = _csv_bytes(TOOL_RESULT_COLUMNS, tool_result_rows)

    _validate_directory_component("experiment ID", identity["experiment_id"])
    _validate_directory_component("campaign ID", identity["campaign_id"])
    _validate_directory_component("merge ID", identity["merge_id"])
    output_dir = (
        Path(output_root)
        / identity["experiment_id"]
        / f"campaign_{identity['campaign_id']}"
        / f"merge_{identity['merge_id']}"
    )

    source: dict[str, Any] = {
        "kind": "campaign",
        "campaign_id": identity["campaign_id"],
        "experiment_id": identity["experiment_id"],
        "design_digest": identity["design_digest"],
        "package_sha256": identity["package_sha256"],
        "contract_sha256": identity["contract_sha256"],
        "campaign_manifest": "campaign.json",
        "campaign_manifest_sha256": sha256_file(campaign_manifest_path),
        "merge_id": identity["merge_id"],
        "merge_manifest": "campaign_merge.json",
        "merge_manifest_sha256": sha256_file(merge_manifest_path),
        "merge_completeness": merge["merge"]["completeness"],
        "merge_population": dict(merge["population"]),
    }
    adjudication = merge.get("adjudication")
    if isinstance(adjudication, Mapping):
        source["adjudication"] = dict(adjudication)

    export_manifest = {
        "schema_version": CAMPAIGN_EXPORT_SCHEMA_VERSION,
        "source": source,
        "executions": execution_provenance,
        "tables": {
            "planned_runs": {
                "artifact": "planned_runs.csv",
                "rows": len(planned_rows),
                "sha256": _sha256_bytes(planned_bytes),
            },
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
            "tool_calls": {
                "artifact": "tool_calls.csv",
                "rows": len(tool_call_rows),
                "sha256": _sha256_bytes(tool_calls_bytes),
            },
            "tool_results": {
                "artifact": "tool_results.csv",
                "rows": len(tool_result_rows),
                "sha256": _sha256_bytes(tool_results_bytes),
            },
        },
    }
    manifest_bytes = yaml.safe_dump(
        export_manifest,
        sort_keys=False,
        allow_unicode=False,
    ).encode("utf-8")
    _report_progress(
        progress,
        (
            f"publishing {len(planned_rows)} planned-run, {len(run_rows)} attempt, "
            f"{len(judgment_rows)} judgment, {len(tool_call_rows)} tool-call, and "
            f"{len(tool_result_rows)} tool-result row(s)..."
        ),
    )
    _publish_export(
        output_dir,
        {
            "planned_runs.csv": planned_bytes,
            "runs.csv": runs_bytes,
            "judgments.csv": judgments_bytes,
            "tool_calls.csv": tool_calls_bytes,
            "tool_results.csv": tool_results_bytes,
            "export_manifest.yaml": manifest_bytes,
        },
    )

    return campaign_analysis_export_result(
        campaign_id=identity["campaign_id"],
        merge_id=identity["merge_id"],
        experiment_id=identity["experiment_id"],
        output_dir=output_dir,
        planned_runs_path=output_dir / "planned_runs.csv",
        runs_path=output_dir / "runs.csv",
        judgments_path=output_dir / "judgments.csv",
        tool_calls_path=output_dir / "tool_calls.csv",
        tool_results_path=output_dir / "tool_results.csv",
        manifest_path=output_dir / "export_manifest.yaml",
        planned_run_rows=len(planned_rows),
        run_rows=len(run_rows),
        judgment_rows=len(judgment_rows),
        tool_call_rows=len(tool_call_rows),
        tool_result_rows=len(tool_result_rows),
    )


def _validate_campaign_merge_binding(
    *,
    campaign_path: Path,
    campaign_manifest_path: Path,
    campaign: dict[str, Any],
    merge_path: Path,
    merge_manifest_path: Path,
    merge: dict[str, Any],
) -> dict[str, str]:
    campaign_identity = campaign["campaign"]
    package = campaign["package"]
    merge_identity = merge["merge"]
    expected = {
        "campaign_id": campaign_identity["id"],
        "experiment_id": campaign_identity["experiment_id"],
        "design_digest": campaign_identity["design_digest"],
        "contract_sha256": package["contract_sha256"],
        "package_sha256": package["sha256"],
    }
    actual = {
        key: merge_identity[key]
        for key in expected
    }
    if actual != expected:
        raise analysis_export_error(
            "Campaign merge identity/provenance does not match the supplied campaign."
        )
    if merge_identity["campaign_manifest_sha256"] != sha256_file(campaign_manifest_path):
        raise analysis_export_error(
            "Campaign merge does not bind the supplied campaign.json bytes."
        )

    merge_campaign_path = merge_path / "campaign.json"
    if not merge_campaign_path.is_file() or merge_campaign_path.is_symlink():
        raise analysis_export_error(
            f"Campaign merge snapshot is missing safely: '{merge_campaign_path}'."
        )
    if sha256_file(merge_campaign_path) != merge_identity["campaign_manifest_sha256"]:
        raise analysis_export_error(
            "Merged campaign.json snapshot does not match the merge provenance."
        )

    population = merge["population"]
    if population["total"] != campaign_identity["run_count"]:
        raise analysis_export_error(
            "Campaign merge population.total does not match campaign run_count."
        )
    if population["observed"] != len(merge["observations"]):
        raise analysis_export_error(
            "Campaign merge population.observed does not match observations length."
        )
    if population["pending"] != population["total"] - population["observed"]:
        raise analysis_export_error(
            "Campaign merge population counts are inconsistent."
        )
    expected_completeness = "complete" if population["pending"] == 0 else "partial"
    if merge_identity["completeness"] != expected_completeness:
        raise analysis_export_error(
            "Campaign merge completeness does not match its population counts."
        )

    adjudication = merge.get("adjudication")
    if adjudication is not None:
        if not isinstance(adjudication, Mapping):
            raise analysis_export_error("Campaign merge adjudication provenance is malformed.")
        relative = adjudication.get("manifest")
        expected_sha256 = adjudication.get("manifest_sha256")
        if not isinstance(relative, str) or not isinstance(expected_sha256, str):
            raise analysis_export_error("Campaign merge adjudication provenance is incomplete.")
        adjudication_path = _safe_source_path(campaign_path, relative, "adjudication manifest")
        if not adjudication_path.is_file():
            raise analysis_export_error(
                f"Campaign adjudication manifest is missing: '{adjudication_path}'."
            )
        if sha256_file(adjudication_path) != expected_sha256:
            raise analysis_export_error(
                "Campaign adjudication manifest SHA-256 does not match merge provenance."
            )
        adjudication_manifest = _load_json_mapping(
            adjudication_path,
            "campaign adjudication manifest",
        )
        try:
            validate_campaign_adjudication_manifest(adjudication_manifest)
        except ValueError as exc:
            raise analysis_export_error(str(exc)) from exc
        adjudication_identity = adjudication_manifest["adjudication"]
        if (
            adjudication_identity["id"] != adjudication.get("id")
            or adjudication_identity["campaign_id"] != expected["campaign_id"]
            or adjudication_identity["experiment_id"] != expected["experiment_id"]
            or adjudication_identity["adjudicator_package_sha256"]
            != adjudication.get("adjudicator_package_sha256")
            or adjudication_identity["adjudication_component_sha256"]
            != adjudication.get("adjudication_component_sha256")
        ):
            raise analysis_export_error(
                "Campaign adjudication provenance does not match the merge."
            )

    return {
        **expected,
        "merge_id": merge_identity["id"],
    }


def _planned_runs(
    campaign: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_index: dict[int, dict[str, Any]] = {}
    runs = campaign["runs"]
    if len(runs) != campaign["campaign"]["run_count"]:
        raise analysis_export_error(
            "Campaign runs length does not match campaign.run_count."
        )
    for entry in runs:
        logical_run_id = entry["run_id"]
        run_index = entry["run_index"]
        if logical_run_id in by_id or run_index in by_index:
            raise analysis_export_error(
                "Campaign contains duplicate logical run identity."
            )
        by_id[logical_run_id] = dict(entry)
        by_index[run_index] = dict(entry)
    if sorted(by_index) != list(range(len(runs))):
        raise analysis_export_error(
            "Campaign run_index values must form a contiguous zero-based population."
        )
    return by_id, by_index


def _load_accepted_observations(
    *,
    campaign_path: Path,
    merge_path: Path,
    merge: Mapping[str, Any],
    planned_by_id: Mapping[str, dict[str, Any]],
    contract: dict[str, Any],
    record_schema: dict[str, Any],
    expected_contract_sha256: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    accepted: dict[str, dict[str, Any]] = {}
    records: dict[str, dict[str, Any]] = {}
    seen_run_ids: set[str] = set()
    seen_results: set[str] = set()
    for position, observation_value in enumerate(merge["observations"]):
        observation = dict(observation_value)
        logical_run_id = observation["logical_run_id"]
        if logical_run_id not in planned_by_id:
            raise analysis_export_error(
                f"Campaign merge observations[{position}] references an unknown logical run."
            )
        if logical_run_id in accepted:
            raise analysis_export_error(
                f"Campaign merge contains duplicate observation for '{logical_run_id}'."
            )
        planned = planned_by_id[logical_run_id]
        for key in ("run_index", "subject_id", "scenario_id", "repetition"):
            if observation[key] != planned[key]:
                raise analysis_export_error(
                    f"Campaign merge observation '{logical_run_id}' does not match planned {key}."
                )
        if observation["source_run_id"] in seen_run_ids:
            raise analysis_export_error("Campaign merge contains duplicate accepted source run_id.")
        if observation["merged_result"] in seen_results:
            raise analysis_export_error("Campaign merge contains duplicate merged_result path.")
        seen_run_ids.add(observation["source_run_id"])
        seen_results.add(observation["merged_result"])

        campaign_execution_path = _safe_source_path(
            campaign_path,
            observation["source_campaign_execution"],
            "source Campaign Execution",
        )
        if not campaign_execution_path.is_file() or campaign_execution_path.is_symlink():
            raise analysis_export_error(
                f"Source Campaign Execution does not exist safely: '{campaign_execution_path}'."
            )
        if sha256_file(campaign_execution_path) != observation["source_campaign_execution_sha256"]:
            raise analysis_export_error(
                f"Source Campaign Execution changed for '{logical_run_id}'."
            )
        event_path = campaign_execution_path.parent / "campaign_events.jsonl"
        if not event_path.is_file() or event_path.is_symlink():
            raise analysis_export_error(
                f"Source campaign event journal does not exist safely: '{event_path}'."
            )
        if sha256_file(event_path) != observation["source_event_journal_sha256"]:
            raise analysis_export_error(
                f"Source campaign event journal changed for '{logical_run_id}'."
            )

        source_result = _safe_source_path(
            campaign_path,
            observation["source_result"],
            "source result",
        )
        source_record_path = source_result / "run_record.yaml"
        if not source_record_path.is_file() or source_record_path.is_symlink():
            raise analysis_export_error(
                f"Source Run Record does not exist safely: '{source_record_path}'."
            )
        if sha256_file(source_record_path) != observation["source_run_record_sha256"]:
            raise analysis_export_error(
                f"Source Run Record changed for '{logical_run_id}'."
            )
        if (
            merge.get("adjudication") is None
            and observation["source_run_record_sha256"]
            != observation["merged_run_record_sha256"]
        ):
            raise analysis_export_error(
                f"Unadjudicated merge changed Run Record '{logical_run_id}'."
            )

        merged_result = _safe_source_path(
            merge_path,
            observation["merged_result"],
            "merged result",
        )
        merged_record_path = merged_result / "run_record.yaml"
        if not merged_record_path.is_file() or merged_record_path.is_symlink():
            raise analysis_export_error(
                f"Merged Run Record does not exist safely: '{merged_record_path}'."
            )
        if sha256_file(merged_record_path) != observation["merged_run_record_sha256"]:
            raise analysis_export_error(
                f"Merged Run Record SHA-256 changed for '{logical_run_id}'."
            )
        record = _load_validated_record(
            merged_record_path,
            merged_result,
            contract,
            record_schema,
            expected_contract_sha256,
        )
        run = record["run"]
        if (
            run["id"] != observation["source_run_id"]
            or run["run_index"] != observation["run_index"]
            or run["subject_id"] != observation["subject_id"]
            or run["scenario_id"] != observation["scenario_id"]
            or run["repetition"] != observation["repetition"]
            or record["execution"]["validity"] != "valid"
        ):
            raise analysis_export_error(
                f"Merged Run Record does not match accepted observation '{logical_run_id}'."
            )
        accepted[logical_run_id] = observation
        records[logical_run_id] = record
    return accepted, records


def _load_campaign_attempt_history(
    *,
    campaign_path: Path,
    campaign: Mapping[str, Any],
    planned_by_index: Mapping[int, dict[str, Any]],
    contract: dict[str, Any],
    record_schema: dict[str, Any],
    expected_contract_sha256: str,
) -> tuple[list[dict[str, Any]], list[_campaign_attempt]]:
    executions_root = campaign_path / "executions"
    if not executions_root.exists():
        return [], []
    if not executions_root.is_dir() or executions_root.is_symlink():
        raise analysis_export_error(
            f"Campaign executions root does not exist safely: '{executions_root}'."
        )

    manifests: list[tuple[str, str, Path, dict[str, Any]]] = []
    for session_dir in sorted(executions_root.glob("execution_*")):
        if not session_dir.is_dir() or session_dir.is_symlink():
            continue
        campaign_execution_path = session_dir / "campaign_execution.json"
        if not campaign_execution_path.is_file() or campaign_execution_path.is_symlink():
            continue
        value = _load_json_mapping(campaign_execution_path, "campaign execution")
        try:
            validate_campaign_execution_manifest(value)
        except ValueError as exc:
            raise analysis_export_error(str(exc)) from exc
        execution = value["execution"]
        if execution["campaign_id"] != campaign["campaign"]["id"]:
            raise analysis_export_error(
                f"Campaign Execution '{campaign_execution_path}' belongs to another campaign."
            )
        if execution["experiment_id"] != campaign["campaign"]["experiment_id"]:
            raise analysis_export_error(
                f"Campaign Execution '{campaign_execution_path}' belongs to another experiment."
            )
        if len(execution["selected_run_indices"]) != len(execution["selected_run_ids"]):
            raise analysis_export_error(
                f"Campaign Execution '{campaign_execution_path}' selected-run identity is inconsistent."
            )
        for run_index, logical_run_id in zip(
            execution["selected_run_indices"], execution["selected_run_ids"]
        ):
            planned = planned_by_index.get(run_index)
            if planned is None or planned["run_id"] != logical_run_id:
                raise analysis_export_error(
                    f"Campaign Execution '{campaign_execution_path}' selected-run identity does not match the campaign."
                )
        manifests.append(
            (
                execution["started_at"],
                execution["id"],
                session_dir,
                value,
            )
        )

    manifests.sort(key=lambda item: (item[0], item[1]))
    execution_provenance: list[dict[str, Any]] = []
    attempts: list[_campaign_attempt] = []
    seen_execution_ids: set[str] = set()
    seen_run_ids: set[str] = set()
    seen_source_results: set[str] = set()

    for order, (_, execution_id, session_dir, campaign_execution) in enumerate(
        manifests,
        start=1,
    ):
        if execution_id in seen_execution_ids:
            raise analysis_export_error(
                f"Campaign contains duplicate execution ID '{execution_id}'."
            )
        seen_execution_ids.add(execution_id)
        execution = campaign_execution["execution"]
        source_artifacts = [
            ("completed", execution.get("experiment_execution_artifact")),
            ("interrupted", execution.get("experiment_interruption_artifact")),
            ("failed", execution.get("experiment_failure_artifact")),
        ]
        source_artifacts = [item for item in source_artifacts if isinstance(item[1], str)]
        if len(source_artifacts) > 1:
            raise analysis_export_error(
                f"Campaign Execution '{execution_id}' references multiple execution manifests."
            )

        provenance: dict[str, Any] = {
            "execution_id": execution_id,
            "status": execution["status"],
            "order": order,
            "started_at": execution["started_at"],
            "ended_at": execution.get("ended_at", ""),
            "campaign_execution": _relative_posix(campaign_path, session_dir / "campaign_execution.json"),
            "campaign_execution_sha256": sha256_file(session_dir / "campaign_execution.json"),
            "event_journal": _relative_posix(campaign_path, session_dir / "campaign_events.jsonl"),
            "event_journal_sha256": sha256_file(session_dir / "campaign_events.jsonl"),
        }

        if source_artifacts:
            _, source_name = source_artifacts[0]
            assert isinstance(source_name, str)
            source_manifest_path = _safe_source_path(
                session_dir,
                source_name,
                "source execution manifest",
            )
            if not source_manifest_path.is_file() or source_manifest_path.is_symlink():
                raise analysis_export_error(
                    f"Source execution manifest does not exist safely: '{source_manifest_path}'."
                )
            source_manifest = _load_yaml_mapping(source_manifest_path, "source execution manifest")
            provenance.update(
                {
                    "history_source": "execution_manifest",
                    "source_manifest": _relative_posix(campaign_path, source_manifest_path),
                    "source_manifest_sha256": sha256_file(source_manifest_path),
                }
            )
            attempts.extend(
                _attempts_from_source_manifest(
                    session_dir=session_dir,
                    source_manifest=source_manifest,
                    source_execution_id=execution_id,
                    source_execution_status=execution["status"],
                    source_execution_order=order,
                    campaign=campaign,
                    planned_by_index=planned_by_index,
                    contract=contract,
                    record_schema=record_schema,
                    expected_contract_sha256=expected_contract_sha256,
                    seen_run_ids=seen_run_ids,
                    seen_source_results=seen_source_results,
                )
            )
        else:
            provenance["history_source"] = "campaign_events"
            attempts.extend(
                _attempts_from_event_journal(
                    session_dir=session_dir,
                    campaign_execution=campaign_execution,
                    source_execution_id=execution_id,
                    source_execution_status=execution["status"],
                    source_execution_order=order,
                    planned_by_index=planned_by_index,
                    contract=contract,
                    record_schema=record_schema,
                    expected_contract_sha256=expected_contract_sha256,
                    seen_run_ids=seen_run_ids,
                    seen_source_results=seen_source_results,
                )
            )
        execution_provenance.append(provenance)

    return execution_provenance, attempts


def _attempts_from_source_manifest(
    *,
    session_dir: Path,
    source_manifest: Mapping[str, Any],
    source_execution_id: str,
    source_execution_status: str,
    source_execution_order: int,
    campaign: Mapping[str, Any],
    planned_by_index: Mapping[int, dict[str, Any]],
    contract: dict[str, Any],
    record_schema: dict[str, Any],
    expected_contract_sha256: str,
    seen_run_ids: set[str],
    seen_source_results: set[str],
) -> list[_campaign_attempt]:
    if source_manifest.get("schema_version") != "0.1":
        raise analysis_export_error(
            f"Source execution '{source_execution_id}' schema_version must be '0.1'."
        )
    execution = source_manifest.get("execution")
    slots = source_manifest.get("slots")
    if not isinstance(execution, Mapping) or not isinstance(slots, list):
        raise analysis_export_error(
            f"Source execution '{source_execution_id}' is malformed."
        )
    if execution.get("id") != source_execution_id:
        raise analysis_export_error(
            f"Source execution manifest ID does not match Campaign Execution '{source_execution_id}'."
        )
    if execution.get("experiment_id") != campaign["campaign"]["experiment_id"]:
        raise analysis_export_error(
            f"Source execution '{source_execution_id}' belongs to another experiment."
        )
    if execution.get("contract_sha256") != expected_contract_sha256:
        raise analysis_export_error(
            f"Source execution '{source_execution_id}' Contract SHA-256 does not match the campaign."
        )
    package = execution.get("package")
    if not isinstance(package, Mapping) or package.get("sha256") != campaign["package"]["sha256"]:
        raise analysis_export_error(
            f"Source execution '{source_execution_id}' package SHA-256 does not match the campaign."
        )

    selected_indices = execution.get("selected_run_indices")
    if not isinstance(selected_indices, list) or not all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in selected_indices
    ):
        raise analysis_export_error(
            f"Source execution '{source_execution_id}' selected_run_indices is invalid."
        )
    slot_indices = [slot.get("run_index") if isinstance(slot, Mapping) else None for slot in slots]
    if slot_indices != selected_indices[: len(slot_indices)]:
        raise analysis_export_error(
            f"Source execution '{source_execution_id}' slot ordering is inconsistent."
        )

    result: list[_campaign_attempt] = []
    for slot_position, slot_value in enumerate(slots):
        if not isinstance(slot_value, Mapping):
            raise analysis_export_error(
                f"Source execution '{source_execution_id}' slots[{slot_position}] is malformed."
            )
        slot = dict(slot_value)
        run_index = slot.get("run_index")
        planned = planned_by_index.get(run_index) if isinstance(run_index, int) else None
        if planned is None:
            raise analysis_export_error(
                f"Source execution '{source_execution_id}' contains an unknown run_index."
            )
        for key in ("subject_id", "scenario_id", "repetition"):
            if slot.get(key) != planned[key]:
                raise analysis_export_error(
                    f"Source execution '{source_execution_id}' run_index {run_index} does not match planned {key}."
                )
        attempts = slot.get("attempts")
        if not isinstance(attempts, list):
            raise analysis_export_error(
                f"Source execution '{source_execution_id}' run_index {run_index} attempts is malformed."
            )
        selected_run_id = slot.get("selected_run_id")
        slot_run_ids: list[str] = []
        for attempt_position, attempt_value in enumerate(attempts):
            if not isinstance(attempt_value, Mapping):
                raise analysis_export_error(
                    f"Source execution '{source_execution_id}' contains a malformed attempt."
                )
            attempt = dict(attempt_value)
            expected_attempt = attempt_position + 1
            if attempt.get("attempt") != expected_attempt:
                raise analysis_export_error(
                    f"Source execution '{source_execution_id}' run_index {run_index} attempts must be contiguous from 1."
                )
            result_path = attempt.get("result_path")
            run_id = attempt.get("run_id")
            if not isinstance(result_path, str) or not isinstance(run_id, str) or not run_id:
                raise analysis_export_error(
                    f"Source execution '{source_execution_id}' contains invalid attempt identity."
                )
            result_dir = _safe_source_path(session_dir, result_path, "source result")
            record_path = result_dir / "run_record.yaml"
            record = _load_validated_record(
                record_path,
                result_dir,
                contract,
                record_schema,
                expected_contract_sha256,
            )
            _validate_attempt_record_binding(
                planned=planned,
                attempt=attempt,
                record=record,
                source_execution_id=source_execution_id,
            )
            if run_id in seen_run_ids:
                raise analysis_export_error(f"Campaign history contains duplicate run_id '{run_id}'.")
            source_key = _relative_posix(session_dir.parent.parent, result_dir)
            if source_key in seen_source_results:
                raise analysis_export_error(
                    f"Campaign history contains duplicate source result '{source_key}'."
                )
            seen_run_ids.add(run_id)
            seen_source_results.add(source_key)
            slot_run_ids.append(run_id)
            result.append(
                _campaign_attempt(
                    logical_run_id=planned["run_id"],
                    run_index=run_index,
                    source_execution_id=source_execution_id,
                    source_execution_status=source_execution_status,
                    source_execution_order=source_execution_order,
                    source_attempt=expected_attempt,
                    source_selected=selected_run_id == run_id,
                    source_result_path=source_key,
                    source_result_dir=result_dir,
                    record=record,
                )
            )
        if selected_run_id is not None:
            if not isinstance(selected_run_id, str) or selected_run_id not in slot_run_ids:
                raise analysis_export_error(
                    f"Source execution '{source_execution_id}' selected_run_id does not resolve within run_index {run_index}."
                )
            if slot_run_ids and slot_run_ids[-1] != selected_run_id:
                raise analysis_export_error(
                    f"Source execution '{source_execution_id}' selected_run_id must identify the final persisted attempt."
                )
    return result


def _attempts_from_event_journal(
    *,
    session_dir: Path,
    campaign_execution: Mapping[str, Any],
    source_execution_id: str,
    source_execution_status: str,
    source_execution_order: int,
    planned_by_index: Mapping[int, dict[str, Any]],
    contract: dict[str, Any],
    record_schema: dict[str, Any],
    expected_contract_sha256: str,
    seen_run_ids: set[str],
    seen_source_results: set[str],
) -> list[_campaign_attempt]:
    event_path = session_dir / "campaign_events.jsonl"
    event_log = read_campaign_events(event_path)
    observed = set(campaign_execution["outcomes"]["observed_run_ids"])
    persisted = [event for event in event_log.events if event.get("event") == "attempt_persisted"]
    result: list[_campaign_attempt] = []
    seen_keys: set[tuple[int, int]] = set()
    for event in persisted:
        run_index = event.get("run_index")
        source_attempt = event.get("attempt")
        result_path = event.get("result_path")
        if (
            not isinstance(run_index, int)
            or isinstance(run_index, bool)
            or not isinstance(source_attempt, int)
            or isinstance(source_attempt, bool)
            or source_attempt < 1
            or not isinstance(result_path, str)
            or not result_path
        ):
            raise analysis_export_error(
                f"Campaign Execution '{source_execution_id}' has malformed attempt_persisted history."
            )
        key = (run_index, source_attempt)
        if key in seen_keys:
            raise analysis_export_error(
                f"Campaign Execution '{source_execution_id}' has duplicate persisted attempt history."
            )
        seen_keys.add(key)
        planned = planned_by_index.get(run_index)
        if planned is None:
            raise analysis_export_error(
                f"Campaign Execution '{source_execution_id}' persisted an unknown run_index {run_index}."
            )
        result_dir = _safe_persisted_result_path(session_dir, result_path)
        record_path = result_dir / "run_record.yaml"
        record = _load_validated_record(
            record_path,
            result_dir,
            contract,
            record_schema,
            expected_contract_sha256,
        )
        run = record["run"]
        realized = record["execution"]
        if (
            run["run_index"] != run_index
            or run["subject_id"] != planned["subject_id"]
            or run["scenario_id"] != planned["scenario_id"]
            or run["repetition"] != planned["repetition"]
            or realized["validity"] != event.get("validity")
            or realized["termination"] != event.get("termination")
        ):
            raise analysis_export_error(
                f"Campaign Execution '{source_execution_id}' event history does not match persisted Run Record."
            )
        run_id = run["id"]
        if run_id in seen_run_ids:
            raise analysis_export_error(f"Campaign history contains duplicate run_id '{run_id}'.")
        source_key = _relative_posix(session_dir.parent.parent, result_dir)
        if source_key in seen_source_results:
            raise analysis_export_error(
                f"Campaign history contains duplicate source result '{source_key}'."
            )
        seen_run_ids.add(run_id)
        seen_source_results.add(source_key)
        result.append(
            _campaign_attempt(
                logical_run_id=planned["run_id"],
                run_index=run_index,
                source_execution_id=source_execution_id,
                source_execution_status=source_execution_status,
                source_execution_order=source_execution_order,
                source_attempt=source_attempt,
                source_selected=(
                    planned["run_id"] in observed and realized["validity"] == "valid"
                ),
                source_result_path=source_key,
                source_result_dir=result_dir,
                record=record,
            )
        )
    result.sort(key=lambda item: (item.run_index, item.source_attempt))
    return result


def _bind_attempts_to_merge(
    attempts: list[_campaign_attempt],
    accepted: Mapping[str, dict[str, Any]],
    merged_records: Mapping[str, dict[str, Any]],
) -> None:
    by_identity = {
        (
            item.logical_run_id,
            item.source_execution_id,
            item.source_attempt,
            item.record["run"]["id"],
        ): item
        for item in attempts
    }
    for logical_run_id, observation in accepted.items():
        key = (
            logical_run_id,
            observation["source_execution_id"],
            observation["source_attempt"],
            observation["source_run_id"],
        )
        item = by_identity.get(key)
        if item is None:
            raise analysis_export_error(
                f"Accepted observation '{logical_run_id}' is absent from Campaign attempt history."
            )
        if not item.source_selected:
            raise analysis_export_error(
                f"Accepted observation '{logical_run_id}' is not selected by its source execution history."
            )
        item.selected = True
        item.authoritative_result_path = observation["merged_result"]
        item.authoritative_record = merged_records[logical_run_id]

    selected_ids = {item.logical_run_id for item in attempts if item.selected}
    if selected_ids != set(accepted):
        raise analysis_export_error(
            "Campaign attempt history and merge accepted-observation set disagree."
        )


def _assign_campaign_attempt_ordinals(attempts: list[_campaign_attempt]) -> None:
    grouped: dict[str, list[_campaign_attempt]] = {}
    for item in attempts:
        grouped.setdefault(item.logical_run_id, []).append(item)
    for logical_run_id, rows in grouped.items():
        rows.sort(key=lambda item: (item.source_execution_order, item.source_attempt))
        for ordinal, item in enumerate(rows, start=1):
            item.attempt = ordinal
        selected = [item for item in rows if item.selected]
        if len(selected) > 1:
            raise analysis_export_error(
                f"Logical run '{logical_run_id}' has multiple merge-selected attempts."
            )
        if selected and rows[-1] is not selected[0]:
            raise analysis_export_error(
                f"Logical run '{logical_run_id}' merge-selected attempt is not the final Campaign attempt."
            )


def _build_planned_rows(
    identity: Mapping[str, str],
    campaign: Mapping[str, Any],
    accepted: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for planned in campaign["runs"]:
        observation = accepted.get(planned["run_id"])
        rows.append(
            {
                "export_schema_version": CAMPAIGN_EXPORT_SCHEMA_VERSION,
                "campaign_id": identity["campaign_id"],
                "experiment_id": identity["experiment_id"],
                "design_digest": identity["design_digest"],
                "contract_sha256": identity["contract_sha256"],
                "package_sha256": identity["package_sha256"],
                "run_index": planned["run_index"],
                "logical_run_id": planned["run_id"],
                "subject_id": planned["subject_id"],
                "scenario_id": planned["scenario_id"],
                "repetition": planned["repetition"],
                "materialization_key": planned["materialization_key"],
                "observed": observation is not None,
                "accepted_run_id": observation["source_run_id"] if observation else "",
                "source_execution_id": observation["source_execution_id"] if observation else "",
                "source_execution_status": observation["source_execution_status"] if observation else "",
                "source_attempt": observation["source_attempt"] if observation else "",
            }
        )
    return rows


def _build_run_row(
    identity: Mapping[str, str],
    item: _campaign_attempt,
) -> dict[str, Any]:
    record = item.authoritative_record if item.selected else item.record
    assert record is not None
    run = record["run"]
    realized = record["execution"]
    provenance = record["provenance"]
    failure = realized.get("failure", {})
    subject = provenance.get("subject", {})
    agent = subject.get("agent", {})
    model = subject.get("model", {})
    environment = provenance.get("environment", {})
    return {
        "export_schema_version": CAMPAIGN_EXPORT_SCHEMA_VERSION,
        "campaign_id": identity["campaign_id"],
        "experiment_id": identity["experiment_id"],
        "design_digest": identity["design_digest"],
        "contract_sha256": identity["contract_sha256"],
        "package_sha256": identity["package_sha256"],
        "source_execution_id": item.source_execution_id,
        "source_execution_status": item.source_execution_status,
        "source_execution_order": item.source_execution_order,
        "source_attempt": item.source_attempt,
        "attempt": item.attempt,
        "source_selected": item.source_selected,
        "selected": item.selected,
        "logical_run_id": item.logical_run_id,
        "run_index": run["run_index"],
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
        "environment_configuration_json": _json_cell(environment.get("configuration", {})),
        "evidence_count": len(record["evidence"]),
        "judgment_count": len(record["judgments"]),
        "judgment_source": "merge" if item.selected else "source",
        "source_result_path": item.source_result_path,
        "authoritative_result_path": item.authoritative_result_path,
    }


def _build_judgment_rows(
    identity: Mapping[str, str],
    contract: dict[str, Any],
    attempts: Iterable[_campaign_attempt],
) -> Iterable[dict[str, Any]]:
    scenarios = {scenario["id"]: scenario for scenario in contract["scenarios"]}
    requirements = {requirement["id"]: requirement for requirement in contract["requirements"]}
    checks = {check["id"]: check for check in contract["checks"]}
    for item in attempts:
        record = item.authoritative_record if item.selected else item.record
        assert record is not None
        run = record["run"]
        realized = record["execution"]
        opportunities = {
            opportunity["id"]: opportunity
            for opportunity in scenarios[run["scenario_id"]]["opportunities"]
        }
        for judgment in record["judgments"]:
            opportunity = opportunities[judgment["opportunity_id"]]
            requirement = requirements[opportunity["requirement_id"]]
            check = checks[opportunity["check_id"]]
            yield {
                "export_schema_version": CAMPAIGN_EXPORT_SCHEMA_VERSION,
                "campaign_id": identity["campaign_id"],
                "experiment_id": identity["experiment_id"],
                "design_digest": identity["design_digest"],
                "contract_sha256": identity["contract_sha256"],
                "package_sha256": identity["package_sha256"],
                "source_execution_id": item.source_execution_id,
                "source_execution_order": item.source_execution_order,
                "source_attempt": item.source_attempt,
                "attempt": item.attempt,
                "source_selected": item.source_selected,
                "selected": item.selected,
                "logical_run_id": item.logical_run_id,
                "run_index": run["run_index"],
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
                "judgment_source": "merge" if item.selected else "source",
            }


def _tool_identity_row(
    identity: Mapping[str, str],
    item: _campaign_attempt,
) -> dict[str, Any]:
    record = item.record
    run = record["run"]
    realized = record["execution"]
    return {
        "export_schema_version": CAMPAIGN_EXPORT_SCHEMA_VERSION,
        "campaign_id": identity["campaign_id"],
        "experiment_id": identity["experiment_id"],
        "design_digest": identity["design_digest"],
        "contract_sha256": identity["contract_sha256"],
        "package_sha256": identity["package_sha256"],
        "source_execution_id": item.source_execution_id,
        "source_execution_order": item.source_execution_order,
        "source_attempt": item.source_attempt,
        "attempt": item.attempt,
        "source_selected": item.source_selected,
        "selected": item.selected,
        "logical_run_id": item.logical_run_id,
        "run_index": run["run_index"],
        "run_id": run["id"],
        "subject_id": run["subject_id"],
        "scenario_id": run["scenario_id"],
        "repetition": run["repetition"],
        "validity": realized["validity"],
        "termination": realized["termination"],
    }


def _build_tool_call_rows(
    identity: Mapping[str, str],
    attempts: Iterable[_campaign_attempt],
) -> Iterable[dict[str, Any]]:
    for item in attempts:
        values = _load_tool_evidence(item, "tool_calls")
        for call_index, value in enumerate(values, start=1):
            name = value.get("name")
            if not isinstance(name, str) or not name:
                raise analysis_export_error(
                    f"Tool-call evidence for run '{item.record['run']['id']}' contains an invalid name."
                )
            call_id = value.get("id")
            if call_id is not None and not isinstance(call_id, str):
                raise analysis_export_error(
                    f"Tool-call evidence for run '{item.record['run']['id']}' contains a non-string id."
                )
            transport_name = value.get("transport_name", "")
            if transport_name is None:
                transport_name = ""
            if not isinstance(transport_name, str):
                raise analysis_export_error(
                    f"Tool-call evidence for run '{item.record['run']['id']}' contains an invalid transport_name."
                )
            row = _tool_identity_row(identity, item)
            row.update(
                {
                    "call_index": call_index,
                    "call_id": call_id or "",
                    "tool_name": name,
                    "transport_name": transport_name,
                    "input_json": _json_cell(value.get("input", {})),
                    "timestamp": _timestamp_cell(value.get("timestamp")),
                }
            )
            yield row


def _build_tool_result_rows(
    identity: Mapping[str, str],
    attempts: Iterable[_campaign_attempt],
) -> Iterable[dict[str, Any]]:
    for item in attempts:
        values = _load_tool_evidence(item, "tool_results")
        for result_index, value in enumerate(values, start=1):
            name = value.get("name")
            if not isinstance(name, str) or not name:
                raise analysis_export_error(
                    f"Tool-result evidence for run '{item.record['run']['id']}' contains an invalid name."
                )
            call_id = value.get("id")
            if call_id is not None and not isinstance(call_id, str):
                raise analysis_export_error(
                    f"Tool-result evidence for run '{item.record['run']['id']}' contains a non-string id."
                )
            transport_name = value.get("transport_name", "")
            if transport_name is None:
                transport_name = ""
            if not isinstance(transport_name, str):
                raise analysis_export_error(
                    f"Tool-result evidence for run '{item.record['run']['id']}' contains an invalid transport_name."
                )
            output = value.get("output", "")
            if not isinstance(output, str):
                raise analysis_export_error(
                    f"Tool-result evidence for run '{item.record['run']['id']}' contains a non-string output."
                )
            is_error = value.get("is_error")
            if not isinstance(is_error, bool):
                raise analysis_export_error(
                    f"Tool-result evidence for run '{item.record['run']['id']}' contains a non-boolean is_error."
                )
            row = _tool_identity_row(identity, item)
            row.update(
                {
                    "result_index": result_index,
                    "tool_call_id": call_id or "",
                    "tool_name": name,
                    "transport_name": transport_name,
                    "output": output,
                    "is_error": is_error,
                    "timestamp": _timestamp_cell(value.get("timestamp")),
                }
            )
            yield row


def _load_tool_evidence(item: _campaign_attempt, channel: str) -> list[dict[str, Any]]:
    matches = [
        entry
        for entry in item.record.get("evidence", ())
        if isinstance(entry, Mapping) and entry.get("channel") == channel
    ]
    if not matches:
        return []
    if len(matches) != 1:
        raise analysis_export_error(
            f"Run '{item.record['run']['id']}' contains multiple '{channel}' evidence artifacts."
        )
    artifact = matches[0].get("artifact")
    if not isinstance(artifact, str):
        raise analysis_export_error(
            f"Run '{item.record['run']['id']}' contains malformed '{channel}' evidence metadata."
        )
    path = _safe_source_path(item.source_result_dir, artifact, f"{channel} evidence")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise analysis_export_error(
            f"Could not decode '{channel}' evidence for run '{item.record['run']['id']}': {exc}"
        ) from exc
    if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
        raise analysis_export_error(
            f"'{channel}' evidence for run '{item.record['run']['id']}' must be a JSON object array."
        )
    return value


def _timestamp_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    raise analysis_export_error("Tool trajectory timestamp must be scalar or null.")


def _ordered_attempts(attempts: list[_campaign_attempt]) -> list[_campaign_attempt]:
    return sorted(
        attempts,
        key=lambda item: (item.run_index, item.attempt, item.source_execution_order),
    )


def _load_validated_record(
    record_path: Path,
    result_dir: Path,
    contract: dict[str, Any],
    record_schema: dict[str, Any],
    expected_contract_sha256: str,
) -> dict[str, Any]:
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
    _validate_record_artifacts(result_dir, record)
    return record


def _validate_record_artifacts(result_dir: Path, record: Mapping[str, Any]) -> None:
    for collection, label in (("evidence", "Evidence"), ("diagnostics", "Diagnostic")):
        for entry in record.get(collection, ()):
            if not isinstance(entry, Mapping):
                raise analysis_export_error(
                    f"Run Record '{result_dir / 'run_record.yaml'}' has malformed {collection}."
                )
            artifact = entry.get("artifact")
            expected = entry.get("sha256")
            if not isinstance(artifact, str) or not isinstance(expected, str):
                raise analysis_export_error(
                    f"Run Record '{result_dir / 'run_record.yaml'}' has malformed {collection} metadata."
                )
            path = _safe_source_path(result_dir, artifact, f"{label.lower()} artifact")
            if not path.is_file() or path.is_symlink():
                raise analysis_export_error(f"{label} artifact does not exist safely: '{path}'.")
            if sha256_file(path) != expected:
                raise analysis_export_error(f"{label} artifact SHA-256 mismatch: '{path}'.")


def _validate_attempt_record_binding(
    *,
    planned: Mapping[str, Any],
    attempt: Mapping[str, Any],
    record: Mapping[str, Any],
    source_execution_id: str,
) -> None:
    run = record["run"]
    realized = record["execution"]
    expected = {
        "run_index": planned["run_index"],
        "subject_id": planned["subject_id"],
        "scenario_id": planned["scenario_id"],
        "repetition": planned["repetition"],
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
        "validity": realized.get("validity"),
        "termination": realized.get("termination"),
    }
    if actual != expected:
        raise analysis_export_error(
            f"Source execution '{source_execution_id}' attempt metadata does not match its Run Record."
        )


def _load_json_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise analysis_export_error(f"Could not load {label} '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise analysis_export_error(f"{label.capitalize()} '{path}' must contain a mapping.")
    return value


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise analysis_export_error(f"Could not load {label} '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise analysis_export_error(f"{label.capitalize()} '{path}' must contain a mapping.")
    return value


def _safe_source_path(root: Path, reference: str, label: str) -> Path:
    if not isinstance(reference, str) or not reference or "\\" in reference:
        raise analysis_export_error(f"Unsafe {label} path: '{reference}'.")
    relative = Path(reference)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise analysis_export_error(f"Unsafe {label} path: '{reference}'.")
    root_resolved = root.resolve()
    candidate = root_resolved
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise analysis_export_error(f"Refusing symbolic-link {label}: '{candidate}'.")
    try:
        candidate.resolve().relative_to(root_resolved)
    except ValueError as exc:
        raise analysis_export_error(f"{label.capitalize()} escapes source root: '{reference}'.") from exc
    return candidate


def _safe_persisted_result_path(session_dir: Path, reference: str) -> Path:
    value = Path(reference)
    if value.is_absolute():
        try:
            relative = value.resolve().relative_to(session_dir.resolve())
        except ValueError as exc:
            raise analysis_export_error(
                f"Persisted result path escapes Campaign Execution: '{reference}'."
            ) from exc
        return _safe_source_path(session_dir, relative.as_posix(), "persisted result")
    return _safe_source_path(session_dir, reference, "persisted result")


def _relative_posix(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise analysis_export_error(
            f"Campaign source artifact '{path}' escapes campaign root '{root}'."
        ) from exc
