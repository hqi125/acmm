from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Callable, Mapping
import uuid

from acmm.evidence.interpretation import interpretation_detail, interpret_scenario_evidence
from acmm.integration_api import (
    tool_result_error_interpreter,
    tool_result_interpretation_context,
)
from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    deferred_run_finalizer,
    evaluation_context,
    executed_run,
    execution_failure,
    judgment_result,
    observed_environment,
    prepared_environment,
    runtime_run_context,
    runtime_run_finalization,
    runtime_run_recovery_error,
    runtime_subject_admission,
)
from acmm.runtime.interruption import protect_interruption_cleanup
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
    lifecycle_session_handle: Any = None,
    lifecycle_context: runtime_run_context | None = None,
    event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
) -> executed_run:
    clock = now if now is not None else _utc_now
    make_run_id = run_id_factory if run_id_factory is not None else _new_run_id
    emit = event_sink if event_sink is not None else lambda event, payload: None

    subject = _find_by_id(contract["subjects"], planned.subject_id)
    contract_scenario = _find_by_id(contract["scenarios"], planned.scenario_id)

    started_at = clock()

    provenance: dict[str, Any] = {
        "harness": {
            "version": harness_version,
        }
    }

    captured: tuple[captured_evidence, ...] = ()
    observed_termination = "harness_error"
    environment_session: _environment_session | None = None
    subject_admission: runtime_subject_admission | None = None
    subject_execution_interrupted = False
    scenario = contract_scenario

    try:
        emit("environment_prepare_started", {})
        environment_session = _prepare_environment(
            contract,
            contract_scenario,
            registry,
            lifecycle_session_handle=lifecycle_session_handle,
            lifecycle_context=lifecycle_context,
        )
        emit("environment_prepared", {})
        provenance["environment"] = dict(environment_session.provenance)
        captured = environment_session.evidence

        scenario = _scenario_with_task_bindings(
            contract_scenario,
            environment_session.task_bindings,
        )

        if environment_session.admission_requests:
            controller = registry.admission_controller
            if controller is None:
                raise RuntimeError(
                    "Prepared environment requires subject admission, but no "
                    "runtime subject admission controller is configured."
                )
            integration_ids = [
                request.integration_id
                for request in environment_session.admission_requests
            ]
            emit(
                "subject_admission_started",
                {"integration_ids": integration_ids},
            )
            subject_admission = controller.admit(
                planned.subject_id,
                environment_session.admission_requests,
            )
            if not isinstance(subject_admission, runtime_subject_admission):
                raise TypeError(
                    "Runtime subject admission controller admit() must return "
                    "runtime_subject_admission."
                )
            provenance["admission"] = dict(subject_admission.provenance)
            emit(
                "subject_admitted",
                {"integration_ids": integration_ids},
            )

        adapter = registry.subject_adapters[planned.subject_id]
        adapter_subject = _subject_with_configuration_overlay(
            subject,
            registry.subject_configuration_overlays.get(planned.subject_id),
        )
        try:
            emit("subject_execution_started", {})
            adapter_result = adapter.execute(
                adapter_subject,
                scenario,
                environment_session.handle,
                contract["run_protocol"]["termination"],
            )
            emit(
                "subject_execution_ended",
                {"termination": adapter_result.termination},
            )
        except KeyboardInterrupt:
            subject_execution_interrupted = True
            raise
        finally:
            if subject_admission is not None:
                cleanup_scope = (
                    protect_interruption_cleanup()
                    if subject_execution_interrupted
                    else nullcontext()
                )
                with cleanup_scope:
                    controller = registry.admission_controller
                    assert controller is not None
                    emit("subject_revocation_started", {})
                    revocation = controller.revoke(
                        planned.subject_id,
                        subject_admission,
                    )
                    subject_admission = None
                    if revocation is not None and not isinstance(revocation, Mapping):
                        raise TypeError(
                            "Runtime subject admission controller revoke() must return "
                            "a mapping or None."
                        )
                    emit(
                        "subject_revoked",
                        {"detail": dict(revocation or {})},
                    )

        adapter_evidence = _apply_tool_result_error_interpreters(
            adapter_result.evidence,
            subject_id=planned.subject_id,
            interpreters=registry.tool_result_error_interpreters,
        )
        captured += adapter_evidence
        observed_termination = adapter_result.termination

        if adapter_result.subject_provenance is not None:
            provenance["subject"] = adapter_result.subject_provenance

        observation = _observe_environment(
            environment_session,
            scenario,
        )
        captured += observation.evidence

        if not environment_session.uses_runtime_lifecycle:
            _finalize_environment(
                environment_session,
                scenario,
                contract["run_protocol"]["reset"],
                outcome=None,
            )
            environment_session = None

        if adapter_result.failure is not None:
            ended_at = clock()
            result = _build_invalid_run(
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
            return _attach_deferred_finalization(
                result,
                environment_session,
                scenario,
                contract["run_protocol"]["reset"],
            )

        if adapter_result.termination == "harness_error":
            ended_at = clock()
            result = _build_invalid_run(
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
            return _attach_deferred_finalization(
                result,
                environment_session,
                scenario,
                contract["run_protocol"]["reset"],
            )

        if "subject" not in provenance:
            ended_at = clock()
            result = _build_invalid_run(
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
            return _attach_deferred_finalization(
                result,
                environment_session,
                scenario,
                contract["run_protocol"]["reset"],
            )

        interpreted_evidence = interpret_scenario_evidence(scenario, captured)
        judgments = _evaluate_opportunities(
            contract,
            subject,
            scenario,
            captured,
            interpreted_evidence,
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

        result = executed_run(
            record=record,
            artifacts=captured,
        )
        return _attach_deferred_finalization(
            result,
            environment_session,
            scenario,
            contract["run_protocol"]["reset"],
        )

    except runtime_run_recovery_error:
        # Package lifecycle code uses this typed signal only when it has already
        # classified whether provider-visible state is safe for Core to continue.
        # Do not collapse it into an ordinary invalid scientific run.
        raise

    except KeyboardInterrupt:
        with protect_interruption_cleanup():
            if subject_admission is not None and registry.admission_controller is not None:
                try:
                    registry.admission_controller.revoke(
                        planned.subject_id,
                        subject_admission,
                    )
                except Exception:
                    pass
                finally:
                    subject_admission = None
            if environment_session is not None and environment_session.uses_runtime_lifecycle:
                _best_effort_finalize_environment(
                    environment_session,
                    scenario,
                    contract["run_protocol"]["reset"],
                    outcome=runtime_run_finalization(
                        persisted=False,
                        validity=None,
                        termination=observed_termination,
                        interrupted=True,
                    ),
                )
            else:
                _best_effort_finalize_environment(
                    environment_session,
                    scenario,
                    contract["run_protocol"]["reset"],
                    outcome=None,
                )
        raise

    except Exception as exc:
        cleanup_error = None
        if environment_session is not None and not environment_session.uses_runtime_lifecycle:
            cleanup_error = _best_effort_finalize_environment(
                environment_session,
                scenario,
                contract["run_protocol"]["reset"],
                outcome=None,
            )
        ended_at = clock()

        detail = str(exc) or exc.__class__.__name__
        if cleanup_error is not None:
            detail += f" Environment finalization also failed: {cleanup_error}"
        failure = execution_failure(
            kind="orchestration_error",
            message=detail,
        )

        result = _build_invalid_run(
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
        return _attach_deferred_finalization(
            result,
            environment_session,
            scenario,
            contract["run_protocol"]["reset"],
        )


def _apply_tool_result_error_interpreters(
    evidence: tuple[captured_evidence, ...],
    *,
    subject_id: str,
    interpreters: tuple[tool_result_error_interpreter, ...],
) -> tuple[captured_evidence, ...]:
    if not interpreters:
        return evidence

    realized: list[captured_evidence] = []
    for artifact in evidence:
        if artifact.channel not in {"tool_results", "conversation"}:
            realized.append(artifact)
            continue

        try:
            value = json.loads(artifact.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            realized.append(artifact)
            continue

        if artifact.channel == "conversation":
            if isinstance(value, list):
                records = value
            elif isinstance(value, dict) and isinstance(value.get("messages"), list):
                records = value["messages"]
            else:
                realized.append(artifact)
                continue
        elif isinstance(value, list):
            records = value
        else:
            realized.append(artifact)
            continue

        changed = False
        for item in records:
            if not isinstance(item, dict):
                continue

            if artifact.channel == "conversation":
                if item.get("role") != "tool":
                    continue
                tool_name = item.get("name")
                output = item.get("content")
            else:
                tool_name = item.get("name")
                output = item.get("output")

            if not isinstance(tool_name, str) or not tool_name:
                continue

            context = tool_result_interpretation_context(
                subject_id=subject_id,
                tool_name=tool_name,
                output=output,
            )
            detected = _integration_detects_tool_result_error(interpreters, context)
            if detected and item.get("is_error") is not True:
                item["is_error"] = True
                if artifact.channel == "conversation":
                    item["status"] = "failed"
                changed = True

            if item.get("is_error") is True:
                annotations = _integration_tool_result_annotations(
                    interpreters,
                    context,
                )
                for key, annotation_value in annotations.items():
                    if item.get(key) == annotation_value:
                        continue
                    item[key] = annotation_value
                    changed = True

        if not changed:
            realized.append(artifact)
            continue

        content = (
            json.dumps(
                value,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        realized.append(
            captured_evidence(
                id=artifact.id,
                channel=artifact.channel,
                artifact=artifact.artifact,
                content=content,
            )
        )

    return tuple(realized)


def _integration_detects_tool_result_error(
    interpreters: tuple[tool_result_error_interpreter, ...],
    context: tool_result_interpretation_context,
) -> bool:
    for interpreter in interpreters:
        detected = interpreter.detects_error(context)
        if not isinstance(detected, bool):
            raise TypeError(
                "Integration tool-result error interpreter must return bool."
            )
        if detected:
            return True
    return False


def _integration_tool_result_annotations(
    interpreters: tuple[tool_result_error_interpreter, ...],
    context: tool_result_interpretation_context,
) -> dict[str, object]:
    annotations: dict[str, object] = {}
    for interpreter in interpreters:
        callback = getattr(interpreter, "annotations", None)
        if not callable(callback):
            continue
        value = callback(context)
        if not isinstance(value, Mapping):
            raise TypeError(
                "Integration tool-result annotations() must return a mapping."
            )
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise TypeError(
                    "Integration tool-result annotation keys must be non-empty strings."
                )
            annotations[key] = item
    return annotations


def _attach_deferred_finalization(
    result: executed_run,
    session: _environment_session | None,
    scenario: dict[str, Any],
    reset: dict[str, Any],
) -> executed_run:
    if session is None or not session.uses_runtime_lifecycle:
        return result

    def callback(outcome: runtime_run_finalization):
        return _finalize_environment(
            session,
            scenario,
            reset,
            outcome=outcome,
        )

    return executed_run(
        record=result.record,
        artifacts=result.artifacts,
        finalizer=deferred_run_finalizer(callback=callback),
    )


def finalize_executed_run(
    result: executed_run,
    outcome: runtime_run_finalization,
) -> dict[str, Any]:
    if result.finalizer is None:
        return {}
    finalized = result.finalizer.callback(outcome)
    if finalized is None:
        return {}
    if not isinstance(finalized, dict):
        finalized = dict(finalized)
    return finalized


@dataclass(frozen=True)
class _environment_session:
    fixture: object | None
    lifecycle: object | None
    lifecycle_session_handle: Any
    lifecycle_context: runtime_run_context | None
    handle: Any
    provenance: dict[str, Any]
    evidence: tuple[captured_evidence, ...]
    task_bindings: dict[str, str]
    admission_requests: tuple[Any, ...]

    @property
    def uses_runtime_lifecycle(self) -> bool:
        return self.lifecycle is not None


_TASK_BINDING_PATTERN = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")


def _prepare_environment(
    contract: dict[str, Any],
    scenario: dict[str, Any],
    registry: execution_registry,
    *,
    lifecycle_session_handle: Any = None,
    lifecycle_context: runtime_run_context | None = None,
) -> _environment_session:
    environment = scenario.get("environment", {})
    fixture_id = environment.get("fixture")

    if registry.lifecycle is not None:
        if lifecycle_context is None:
            raise ValueError(
                "Runtime execution lifecycle requires a run lifecycle context."
            )
        prepared = registry.lifecycle.run_prepare(
            lifecycle_session_handle,
            lifecycle_context,
            scenario,
            contract["run_protocol"]["reset"],
        )
        if not isinstance(prepared, prepared_environment):
            raise TypeError(
                "Runtime execution lifecycle run_prepare() must return "
                "prepared_environment."
            )
        provenance_fixture_id = (
            fixture_id if isinstance(fixture_id, str) and fixture_id else "runtime_lifecycle"
        )
        return _environment_session(
            fixture=None,
            lifecycle=registry.lifecycle,
            lifecycle_session_handle=lifecycle_session_handle,
            lifecycle_context=lifecycle_context,
            handle=prepared.handle,
            provenance=_environment_record_provenance(
                fixture_id=provenance_fixture_id,
                environment=environment,
                prepared=prepared,
            ),
            evidence=tuple(prepared.evidence),
            task_bindings=dict(prepared.task_bindings),
            admission_requests=tuple(prepared.admission_requests),
        )

    if fixture_id is None:
        return _environment_session(
            fixture=None,
            lifecycle=None,
            lifecycle_session_handle=None,
            lifecycle_context=None,
            handle=None,
            provenance={
                "configuration": environment.get("configuration", {}),
            },
            evidence=(),
            task_bindings={},
            admission_requests=(),
        )

    fixture = registry.fixtures[fixture_id]
    prepared = fixture.prepare(
        scenario,
        contract["run_protocol"]["reset"],
    )
    if not isinstance(prepared, prepared_environment):
        raise TypeError(
            f"Environment fixture '{fixture_id}' prepare() must return "
            "prepared_environment."
        )

    return _environment_session(
        fixture=fixture,
        lifecycle=None,
        lifecycle_session_handle=None,
        lifecycle_context=None,
        handle=prepared.handle,
        provenance=_environment_record_provenance(
            fixture_id=fixture_id,
            environment=environment,
            prepared=prepared,
        ),
        evidence=tuple(prepared.evidence),
        task_bindings=dict(prepared.task_bindings),
        admission_requests=tuple(prepared.admission_requests),
    )



def _environment_record_provenance(
    *,
    fixture_id: str,
    environment: dict[str, Any],
    prepared: prepared_environment,
) -> dict[str, Any]:
    allowed = {"fixture", "configuration"}
    unexpected = sorted(set(prepared.provenance) - allowed)
    if unexpected:
        raise ValueError(
            "Environment fixture provenance may contain only Run Record v0.1 "
            "fields 'fixture' and 'configuration'; unexpected field(s): "
            + ", ".join(unexpected)
        )

    fixture = prepared.provenance.get("fixture", fixture_id)
    if not isinstance(fixture, str) or not fixture:
        raise ValueError("Environment fixture provenance 'fixture' must be a non-empty string.")

    configuration = prepared.provenance.get(
        "configuration",
        environment.get("configuration", {}),
    )
    if not isinstance(configuration, dict):
        raise ValueError("Environment fixture provenance 'configuration' must be an object.")

    return {
        "fixture": fixture,
        "configuration": dict(configuration),
    }

def _observe_environment(
    session: _environment_session,
    scenario: dict[str, Any],
) -> observed_environment:
    if session.lifecycle is not None:
        assert session.lifecycle_context is not None
        observed = session.lifecycle.run_observe(
            session.lifecycle_session_handle,
            session.lifecycle_context,
            scenario,
            session.handle,
        )
        if not isinstance(observed, observed_environment):
            raise TypeError(
                "Runtime execution lifecycle run_observe() must return "
                "observed_environment."
            )
        return observed

    fixture = session.fixture
    observe = getattr(fixture, "observe", None) if fixture is not None else None
    if not callable(observe):
        return observed_environment()

    observed = observe(scenario, session.handle)
    if not isinstance(observed, observed_environment):
        raise TypeError(
            "Environment fixture observe() must return observed_environment."
        )
    return observed



def _finalize_environment(
    session: _environment_session,
    scenario: dict[str, Any],
    reset: dict[str, Any],
    *,
    outcome: runtime_run_finalization | None,
) -> dict[str, Any]:
    if session.lifecycle is not None:
        if outcome is None:
            raise ValueError(
                "Runtime execution lifecycle finalization requires an explicit outcome."
            )
        assert session.lifecycle_context is not None
        result = session.lifecycle.run_finalize(
            session.lifecycle_session_handle,
            session.lifecycle_context,
            scenario,
            session.handle,
            reset,
            outcome,
        )
    else:
        fixture = session.fixture
        finalize = getattr(fixture, "finalize", None) if fixture is not None else None
        if not callable(finalize):
            return {}
        result = finalize(scenario, session.handle, reset)

    if result is None:
        return {}
    if not isinstance(result, dict):
        result = dict(result)
    return result


def _best_effort_finalize_environment(
    session: _environment_session | None,
    scenario: dict[str, Any],
    reset: dict[str, Any],
    *,
    outcome: runtime_run_finalization | None = None,
) -> str | None:
    if session is None:
        return None
    try:
        _finalize_environment(
            session,
            scenario,
            reset,
            outcome=outcome,
        )
    except Exception as exc:
        return str(exc) or exc.__class__.__name__
    return None



def _scenario_with_task_bindings(
    scenario: dict[str, Any],
    bindings: dict[str, str],
) -> dict[str, Any]:
    prompt = scenario.get("task", {}).get("prompt")
    if not isinstance(prompt, str) or not bindings:
        return scenario

    realized_prompt = prompt
    for name, value in bindings.items():
        if not isinstance(name, str) or _TASK_BINDING_PATTERN.fullmatch("{{" + name + "}}") is None:
            raise ValueError(f"Invalid environment task-binding name: {name!r}.")
        if not isinstance(value, str):
            raise ValueError(
                f"Environment task binding '{name}' must contain a string value."
            )
        realized_prompt = realized_prompt.replace("{{" + name + "}}", value)

    unresolved = sorted(set(_TASK_BINDING_PATTERN.findall(realized_prompt)))
    if unresolved:
        raise ValueError(
            "Scenario task prompt contains unresolved environment bindings: "
            + ", ".join(unresolved)
        )

    realized = dict(scenario)
    task = dict(scenario.get("task", {}))
    task["prompt"] = realized_prompt
    realized["task"] = task
    return realized

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


def _evaluate_opportunities(
    contract: dict[str, Any],
    subject: dict[str, Any],
    scenario: dict[str, Any],
    evidence: tuple[captured_evidence, ...],
    interpreted_evidence: dict[str, Any],
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
                interpreted_evidence=interpreted_evidence,
            )
        )

        judgments.append(
            _judgment_to_record(
                opportunity["id"],
                result,
                interpreted_evidence,
            )
        )

    return judgments


def _judgment_to_record(
    opportunity_id: str,
    result: judgment_result,
    interpreted_evidence: dict[str, Any],
) -> dict[str, Any]:
    judgment: dict[str, Any] = {
        "opportunity_id": opportunity_id,
        "outcome": result.outcome,
        "evidence_ids": list(result.evidence_ids),
    }

    detail_parts: list[str] = []
    if result.detail is not None:
        detail_parts.append(result.detail)
    for evidence_id in result.evidence_ids:
        view = interpreted_evidence.get(evidence_id)
        if view is None:
            continue
        detail = interpretation_detail(view)
        if detail is not None:
            detail_parts.append(detail)
    if detail_parts:
        judgment["detail"] = " ".join(detail_parts)

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
