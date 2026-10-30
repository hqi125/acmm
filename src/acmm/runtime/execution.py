from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any, Callable
import uuid

from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    evaluation_context,
    executed_run,
    execution_failure,
    judgment_result,
)
from acmm.runtime.planner import planned_run
from acmm.runtime.registry import execution_registry


def execute_planned_run(
    contract: dict[str, Any],
    planned: planned_run,
    contract_sha256: str,
    registry: execution_registry,
    harness_version: str,
    now: Callable[[], datetime] | None = None,
    run_id_factory: Callable[[], str] | None = None,
) -> executed_run:
    clock = now if now is not None else _utc_now
    make_run_id = run_id_factory if run_id_factory is not None else _new_run_id

    subject = _find_by_id(contract["subjects"], planned.subject_id)
    scenario = _find_by_id(contract["scenarios"], planned.scenario_id)

    started_at = clock()

    provenance: dict[str, Any] = {
        "harness": {
            "version": harness_version,
        }
    }

    captured: tuple[captured_evidence, ...] = ()
    judgments: list[dict[str, Any]] = []
    observed_termination = "harness_error"

    try:
        environment_handle, environment_provenance = _prepare_environment(
            contract,
            scenario,
            registry,
        )
        provenance["environment"] = environment_provenance

        adapter = registry.subject_adapters[planned.subject_id]
        adapter_subject = _subject_with_configuration_overlay(
            subject,
            registry.subject_configuration_overlays.get(planned.subject_id),
        )
        adapter_result = adapter.execute(
            adapter_subject,
            scenario,
            environment_handle,
            contract["run_protocol"]["termination"],
        )

        captured = adapter_result.evidence
        observed_termination = adapter_result.termination

        if adapter_result.subject_provenance is not None:
            provenance["subject"] = adapter_result.subject_provenance

        if adapter_result.failure is not None:
            ended_at = clock()
            return _build_invalid_run(
                contract,
                planned,
                contract_sha256,
                make_run_id(),
                started_at,
                ended_at,
                adapter_result.termination,
                adapter_result.failure,
                provenance,
                captured,
            )

        if adapter_result.termination == "harness_error":
            ended_at = clock()
            return _build_invalid_run(
                contract,
                planned,
                contract_sha256,
                make_run_id(),
                started_at,
                ended_at,
                "harness_error",
                execution_failure(
                    kind="adapter_protocol_error",
                    message=(
                        "Subject adapter returned harness_error without failure details."
                    ),
                ),
                provenance,
                captured,
            )

        if "subject" not in provenance:
            ended_at = clock()
            return _build_invalid_run(
                contract,
                planned,
                contract_sha256,
                make_run_id(),
                started_at,
                ended_at,
                adapter_result.termination,
                execution_failure(
                    kind="missing_subject_provenance",
                    message=(
                        "Subject adapter completed without realized subject provenance."
                    ),
                ),
                provenance,
                captured,
            )

        judgments = _evaluate_opportunities(
            contract,
            subject,
            scenario,
            captured,
            registry,
        )

        ended_at = clock()

        record = _build_record(
            contract=contract,
            planned=planned,
            contract_sha256=contract_sha256,
            run_id=make_run_id(),
            started_at=started_at,
            ended_at=ended_at,
            termination=adapter_result.termination,
            validity="valid",
            failure=None,
            provenance=provenance,
            evidence=captured,
            judgments=judgments,
        )

        return executed_run(
            record=record,
            artifacts=captured,
        )

    except Exception as exc:
        ended_at = clock()

        failure = execution_failure(
            kind="orchestration_error",
            message=str(exc) or exc.__class__.__name__,
        )

        return _build_invalid_run(
            contract,
            planned,
            contract_sha256,
            make_run_id(),
            started_at,
            ended_at,
            observed_termination,
            failure,
            provenance,
            captured,
        )


def _subject_with_configuration_overlay(
    subject: dict[str, Any],
    overlay: dict[str, Any] | None,
) -> dict[str, Any]:
    if not overlay:
        return subject
    realized = dict(subject)
    configuration = dict(subject.get("configuration", {}))
    configuration.update(overlay)
    realized["configuration"] = configuration
    return realized


def _prepare_environment(
    contract: dict[str, Any],
    scenario: dict[str, Any],
    registry: execution_registry,
) -> tuple[Any, dict[str, Any]]:
    environment = scenario.get("environment", {})
    fixture_id = environment.get("fixture")

    if fixture_id is None:
        return None, {
            "configuration": environment.get("configuration", {}),
        }

    fixture = registry.fixtures[fixture_id]
    prepared = fixture.prepare(
        scenario,
        contract["run_protocol"]["reset"],
    )

    return prepared.handle, prepared.provenance


def _evaluate_opportunities(
    contract: dict[str, Any],
    subject: dict[str, Any],
    scenario: dict[str, Any],
    evidence: tuple[captured_evidence, ...],
    registry: execution_registry,
) -> list[dict[str, Any]]:
    checks = {
        check["id"]: check
        for check in contract["checks"]
    }

    judgments: list[dict[str, Any]] = []

    for opportunity in scenario["opportunities"]:
        check = checks[opportunity["check_id"]]
        evaluator = registry.evaluators[check["evaluator"]]

        result = evaluator.evaluate(
            evaluation_context(
                subject=subject,
                scenario=scenario,
                opportunity=opportunity,
                check=check,
                evidence=evidence,
            )
        )

        judgments.append(
            _judgment_to_record(
                opportunity["id"],
                result,
            )
        )

    return judgments


def _judgment_to_record(
    opportunity_id: str,
    result: judgment_result,
) -> dict[str, Any]:
    judgment: dict[str, Any] = {
        "opportunity_id": opportunity_id,
        "outcome": result.outcome,
        "evidence_ids": list(result.evidence_ids),
    }

    if result.detail is not None:
        judgment["detail"] = result.detail

    return judgment


def _build_invalid_run(
    contract: dict[str, Any],
    planned: planned_run,
    contract_sha256: str,
    run_id: str,
    started_at: datetime,
    ended_at: datetime,
    termination: str,
    failure: execution_failure,
    provenance: dict[str, Any],
    evidence: tuple[captured_evidence, ...],
) -> executed_run:
    record = _build_record(
        contract=contract,
        planned=planned,
        contract_sha256=contract_sha256,
        run_id=run_id,
        started_at=started_at,
        ended_at=ended_at,
        termination=termination,
        validity="invalid",
        failure=failure,
        provenance=provenance,
        evidence=evidence,
        judgments=[],
    )

    return executed_run(
        record=record,
        artifacts=evidence,
    )


def _build_record(
    contract: dict[str, Any],
    planned: planned_run,
    contract_sha256: str,
    run_id: str,
    started_at: datetime,
    ended_at: datetime,
    termination: str,
    validity: str,
    failure: execution_failure | None,
    provenance: dict[str, Any],
    evidence: tuple[captured_evidence, ...],
    judgments: list[dict[str, Any]],
) -> dict[str, Any]:
    execution: dict[str, Any] = {
        "started_at": _format_timestamp(started_at),
        "ended_at": _format_timestamp(ended_at),
        "termination": termination,
        "validity": validity,
    }

    if failure is not None:
        execution["failure"] = {
            "kind": failure.kind,
            "message": failure.message,
        }

    evidence_manifest = [
        {
            "id": item.id,
            "channel": item.channel,
            "artifact": item.artifact,
            "sha256": hashlib.sha256(item.content).hexdigest(),
        }
        for item in evidence
    ]

    return {
        "schema_version": "0.1",
        "run": {
            "id": run_id,
            "experiment_id": contract["experiment"]["id"],
            "run_index": planned.run_index,
            "subject_id": planned.subject_id,
            "scenario_id": planned.scenario_id,
            "repetition": planned.repetition,
            "contract_sha256": contract_sha256,
        },
        "execution": execution,
        "provenance": provenance,
        "evidence": evidence_manifest,
        "judgments": judgments,
    }


def _find_by_id(
    items: list[dict[str, Any]],
    item_id: str,
) -> dict[str, Any]:
    for item in items:
        if item["id"] == item_id:
            return item

    raise KeyError(item_id)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Execution timestamps must be timezone-aware.")

    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_run_id() -> str:
    return str(uuid.uuid4())
