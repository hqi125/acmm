from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from acmm.contract.loader import load_contract
from acmm.record.validator import (
    load_run_record_schema,
    sha256_file,
    validate_run_record,
)
from acmm.runtime.execution import execute_planned_run
from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    evaluation_context,
    execution_failure,
    judgment_result,
    prepared_environment,
)
from acmm.runtime.planner import build_experiment_plan
from acmm.runtime.registry import execution_registry


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    ROOT
    / "experiments"
    / "examples"
    / "magic_number"
    / "experiment.yaml"
)
RECORD_SCHEMA_PATH = (
    ROOT
    / "records"
    / "v0_1"
    / "run_record.schema.json"
)

RUN_ID = "e3f07c5d-44bc-40c8-bf8a-3c8957489fb2"


class fake_fixture:
    def prepare(
        self,
        scenario: dict,
        reset: dict,
    ) -> prepared_environment:
        assert scenario["id"] == "magic_plus_13"
        assert reset["environment"] == "baseline"

        return prepared_environment(
            handle={"magic_number": 87},
            provenance={
                "fixture": "magic_number_environment_v1",
                "configuration": {},
            },
        )


class failing_fixture:
    def prepare(
        self,
        scenario: dict,
        reset: dict,
    ) -> prepared_environment:
        raise RuntimeError("fixture reset failed")


class fake_adapter:
    def __init__(
        self,
        termination: str = "agent_completed",
        failure: execution_failure | None = None,
    ) -> None:
        self.termination = termination
        self.failure = failure

    def execute(
        self,
        subject: dict,
        scenario: dict,
        environment: object,
        termination: dict,
    ) -> adapter_execution_result:
        assert subject["id"] == "hermes_nemotron30b"
        assert scenario["id"] == "magic_plus_13"
        assert environment == {"magic_number": 87}
        assert termination["max_steps"] == 32

        return adapter_execution_result(
            termination=self.termination,
            evidence=(
                captured_evidence(
                    id="conversation",
                    channel="conversation",
                    artifact="evidence/conversation.json",
                    content=b'{"messages":[]}',
                ),
                captured_evidence(
                    id="tool_calls",
                    channel="tool_calls",
                    artifact="evidence/tool_calls.json",
                    content=b'[{"name":"get_magic_number"}]',
                ),
                captured_evidence(
                    id="tool_results",
                    channel="tool_results",
                    artifact="evidence/tool_results.json",
                    content=b'[{"value":87}]',
                ),
                captured_evidence(
                    id="final_response",
                    channel="final_response",
                    artifact="evidence/final_response.txt",
                    content=b"100",
                ),
            ),
            subject_provenance={
                "agent": {
                    "name": "Hermes Agent",
                    "version": "0.21.3",
                },
                "model": {
                    "name": "nemotron-3-nano:30b-cloud",
                    "provider": "ollama_cloud",
                },
                "configuration": {
                    "context_window": 64000,
                },
            },
            failure=self.failure,
        )


class retrieval_evaluator:
    def evaluate(
        self,
        context: evaluation_context,
    ) -> judgment_result:
        assert context.opportunity["id"] == "retrieve"

        return judgment_result(
            outcome="compliant",
            evidence_ids=("tool_calls", "tool_results"),
        )


class addition_evaluator:
    def evaluate(
        self,
        context: evaluation_context,
    ) -> judgment_result:
        assert context.opportunity["id"] == "calculate"

        return judgment_result(
            outcome="compliant",
            evidence_ids=("tool_calls", "tool_results", "final_response"),
        )


class failing_evaluator:
    def evaluate(
        self,
        context: evaluation_context,
    ) -> judgment_result:
        raise RuntimeError("evaluation failed")


def _clock():
    values = iter(
        (
            datetime(2026, 10, 15, 14, 3, 21, tzinfo=timezone.utc),
            datetime(2026, 10, 15, 14, 3, 25, tzinfo=timezone.utc),
        )
    )

    return lambda: next(values)


def _registry(
    adapter: fake_adapter | None = None,
    fixture: object | None = None,
    second_evaluator: object | None = None,
) -> execution_registry:
    return execution_registry(
        subject_adapters={
            "hermes_nemotron30b": adapter or fake_adapter(),
        },
        fixtures={
            "magic_number_environment_v1": fixture or fake_fixture(),
        },
        evaluators={
            "magic_number_retrieval": retrieval_evaluator(),
            "magic_number_addition": second_evaluator or addition_evaluator(),
        },
    )


def _validate_result(contract: dict, result) -> None:
    schema = load_run_record_schema(RECORD_SCHEMA_PATH)
    digest = sha256_file(CONTRACT_PATH)

    validation = validate_run_record(
        result.record,
        schema,
        contract,
        expected_contract_sha256=digest,
    )

    assert validation.is_valid, validation.errors


def test_execute_planned_run_builds_valid_run_record() -> None:
    contract = load_contract(CONTRACT_PATH)
    planned = build_experiment_plan(contract).runs[0]
    digest = sha256_file(CONTRACT_PATH)

    result = execute_planned_run(
        contract=contract,
        planned=planned,
        contract_sha256=digest,
        registry=_registry(),
        harness_version="0.1.0",
        now=_clock(),
        run_id_factory=lambda: RUN_ID,
    )

    assert result.record["run"]["id"] == RUN_ID
    assert result.record["execution"]["validity"] == "valid"
    assert result.record["execution"]["termination"] == "agent_completed"
    assert len(result.artifacts) == 4
    assert len(result.record["judgments"]) == 2

    evidence_by_id = {
        item["id"]: item
        for item in result.record["evidence"]
    }

    assert (
        evidence_by_id["final_response"]["sha256"]
        == "ad57366865126e55649ecb23ae1d48887544976efea46a48eb5d85a6eeb4d306"
    )

    _validate_result(contract, result)


def test_timeout_can_be_scientifically_valid() -> None:
    contract = load_contract(CONTRACT_PATH)
    planned = build_experiment_plan(contract).runs[0]
    digest = sha256_file(CONTRACT_PATH)

    result = execute_planned_run(
        contract=contract,
        planned=planned,
        contract_sha256=digest,
        registry=_registry(adapter=fake_adapter(termination="timeout")),
        harness_version="0.1.0",
        now=_clock(),
        run_id_factory=lambda: RUN_ID,
    )

    assert result.record["execution"]["termination"] == "timeout"
    assert result.record["execution"]["validity"] == "valid"

    _validate_result(contract, result)


def test_adapter_failure_produces_valid_record_of_invalid_execution() -> None:
    contract = load_contract(CONTRACT_PATH)
    planned = build_experiment_plan(contract).runs[0]
    digest = sha256_file(CONTRACT_PATH)

    result = execute_planned_run(
        contract=contract,
        planned=planned,
        contract_sha256=digest,
        registry=_registry(
            adapter=fake_adapter(
                termination="harness_error",
                failure=execution_failure(
                    kind="adapter_connection_error",
                    message="connection lost",
                ),
            )
        ),
        harness_version="0.1.0",
        now=_clock(),
        run_id_factory=lambda: RUN_ID,
    )

    assert result.record["execution"]["validity"] == "invalid"
    assert result.record["execution"]["failure"]["kind"] == "adapter_connection_error"
    assert result.record["judgments"] == []

    _validate_result(contract, result)


def test_subject_configuration_overlay_is_captured_in_realized_provenance() -> None:
    contract = load_contract(CONTRACT_PATH)
    planned = build_experiment_plan(contract).runs[0]

    class overlay_adapter(fake_adapter):
        def execute(self, subject, scenario, environment, termination):
            result = super().execute(subject, scenario, environment, termination)
            provenance = dict(result.subject_provenance or {})
            provenance["configuration"] = dict(subject.get("configuration", {}))
            return adapter_execution_result(
                termination=result.termination,
                evidence=result.evidence,
                subject_provenance=provenance,
                failure=result.failure,
            )

    registry = execution_registry(
        subject_adapters={"hermes_nemotron30b": overlay_adapter()},
        fixtures={"magic_number_environment_v1": fake_fixture()},
        evaluators={
            "magic_number_retrieval": retrieval_evaluator(),
            "magic_number_addition": addition_evaluator(),
        },
        subject_configuration_overlays={
            "hermes_nemotron30b": {
                "llm_baseline_id": "nemotron_3_nano_30b_cloud_v1",
                "llm_minimum_context_window": 64000,
                "llm_requested_context_window": 64000,
            }
        },
    )
    result = execute_planned_run(
        contract,
        planned,
        sha256_file(CONTRACT_PATH),
        registry,
        "0.1.0",
        now=_clock(),
        run_id_factory=lambda: RUN_ID,
    )

    configuration = result.record["provenance"]["subject"]["configuration"]
    assert configuration["llm_baseline_id"] == "nemotron_3_nano_30b_cloud_v1"
    assert configuration["llm_minimum_context_window"] == 64000
    assert configuration["llm_requested_context_window"] == 64000



def test_fixture_failure_allows_partial_invalid_provenance() -> None:
    contract = load_contract(CONTRACT_PATH)
    planned = build_experiment_plan(contract).runs[0]
    digest = sha256_file(CONTRACT_PATH)

    result = execute_planned_run(
        contract=contract,
        planned=planned,
        contract_sha256=digest,
        registry=_registry(fixture=failing_fixture()),
        harness_version="0.1.0",
        now=_clock(),
        run_id_factory=lambda: RUN_ID,
    )

    assert result.record["execution"]["validity"] == "invalid"
    assert result.record["execution"]["termination"] == "harness_error"
    assert result.record["provenance"] == {
        "harness": {
            "version": "0.1.0",
        }
    }

    _validate_result(contract, result)


def test_evaluator_failure_preserves_observed_termination() -> None:
    contract = load_contract(CONTRACT_PATH)
    planned = build_experiment_plan(contract).runs[0]
    digest = sha256_file(CONTRACT_PATH)

    result = execute_planned_run(
        contract=contract,
        planned=planned,
        contract_sha256=digest,
        registry=_registry(second_evaluator=failing_evaluator()),
        harness_version="0.1.0",
        now=_clock(),
        run_id_factory=lambda: RUN_ID,
    )

    assert result.record["execution"]["validity"] == "invalid"
    assert result.record["execution"]["termination"] == "agent_completed"
    assert result.record["execution"]["failure"]["kind"] == "orchestration_error"
    assert len(result.record["evidence"]) == 4
    assert result.record["judgments"] == []

    _validate_result(contract, result)
