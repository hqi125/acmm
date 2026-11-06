from __future__ import annotations

from datetime import datetime, timezone
import json
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
    observed_environment,
    prepared_environment,
)
from acmm.runtime.planner import build_experiment_plan
from acmm.runtime.registry import execution_registry


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "runtime_execution.yaml"
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
        assert scenario["id"] == "synthetic_plus_13"
        assert reset["environment"] == "baseline"

        return prepared_environment(
            handle={"fixture_value": 87},
            provenance={
                "fixture": "synthetic_environment_v1",
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
        assert subject["id"] == "hermes_gpt_oss120b"
        assert scenario["id"] == "synthetic_plus_13"
        assert environment == {"fixture_value": 87}
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
                    content=b'[{"name":"get_fixture_value"}]',
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
                    "name": "gpt-oss:120b-cloud",
                    "provider": "ollama_cloud",
                },
                "configuration": {
                    "context_window": 64000,
                },
            },
            failure=self.failure,
        )


class observed_fixture:
    def __init__(self) -> None:
        self.finalized = False

    def prepare(self, scenario: dict, reset: dict) -> prepared_environment:
        del reset
        assert "{{fixture_value}}" in scenario["task"]["prompt"]
        return prepared_environment(
            handle={"fixture_value": 87},
            provenance={"fixture": "observed_fixture"},
            evidence=(
                captured_evidence(
                    id="initial_environment_state",
                    channel="initial_environment_state",
                    artifact="evidence/initial_environment_state.json",
                    content=b'{"value":87,"phase":"pre"}',
                ),
            ),
            task_bindings={"fixture_value": "87"},
        )

    def observe(self, scenario: dict, handle: object) -> observed_environment:
        assert "{{fixture_value}}" not in scenario["task"]["prompt"]
        assert "87" in scenario["task"]["prompt"]
        assert handle == {"fixture_value": 87}
        return observed_environment(
            evidence=(
                captured_evidence(
                    id="final_environment_state",
                    channel="final_environment_state",
                    artifact="evidence/final_environment_state.json",
                    content=b'{"value":100,"phase":"post"}',
                ),
            ),
        )

    def finalize(self, scenario: dict, handle: object, reset: dict) -> dict:
        del scenario
        del handle
        del reset
        self.finalized = True
        return {"restored": True}


class binding_adapter(fake_adapter):
    def execute(self, subject, scenario, environment, termination):
        assert "{{fixture_value}}" not in scenario["task"]["prompt"]
        assert "87" in scenario["task"]["prompt"]
        return super().execute(subject, scenario, environment, termination)


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
            "hermes_gpt_oss120b": adapter or fake_adapter(),
        },
        fixtures={
            "synthetic_environment_v1": fixture or fake_fixture(),
        },
        evaluators={
            "synthetic_retrieval": retrieval_evaluator(),
            "synthetic_addition": second_evaluator or addition_evaluator(),
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
        subject_adapters={"hermes_gpt_oss120b": overlay_adapter()},
        fixtures={"synthetic_environment_v1": fake_fixture()},
        evaluators={
            "synthetic_retrieval": retrieval_evaluator(),
            "synthetic_addition": addition_evaluator(),
        },
        subject_configuration_overlays={
            "hermes_gpt_oss120b": {
                "llm_baseline_id": "gpt_oss_120b_cloud_v1",
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
    assert configuration["llm_baseline_id"] == "gpt_oss_120b_cloud_v1"
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


def test_environment_observation_task_binding_and_finalization() -> None:
    contract = load_contract(CONTRACT_PATH)
    contract = json.loads(json.dumps(contract))
    contract["scenarios"][0]["task"]["prompt"] += " Fixture={{fixture_value}}."
    contract["evidence_policy"]["capture"].extend(
        ["initial_environment_state", "final_environment_state"]
    )
    planned = build_experiment_plan(contract).runs[0]
    fixture = observed_fixture()

    result = execute_planned_run(
        contract=contract,
        planned=planned,
        contract_sha256="0" * 64,
        registry=_registry(adapter=binding_adapter(), fixture=fixture),
        harness_version="0.1.0",
        now=_clock(),
        run_id_factory=lambda: RUN_ID,
    )

    assert result.record["execution"]["validity"] == "valid"
    assert fixture.finalized is True
    assert [item.id for item in result.artifacts][:1] == ["initial_environment_state"]
    assert result.artifacts[-1].id == "final_environment_state"
    environment = result.record["provenance"]["environment"]
    assert environment == {
        "fixture": "observed_fixture",
        "configuration": {},
    }

    schema = load_run_record_schema(RECORD_SCHEMA_PATH)
    validation = validate_run_record(
        result.record,
        schema,
        contract,
        expected_contract_sha256="0" * 64,
    )
    assert validation.is_valid, validation.errors


def test_environment_fixture_rejects_non_record_provenance_fields() -> None:
    contract = load_contract(CONTRACT_PATH)
    planned = build_experiment_plan(contract).runs[0]

    class extra_provenance_fixture(fake_fixture):
        def prepare(self, scenario, reset):
            base = super().prepare(scenario, reset)
            return prepared_environment(
                handle=base.handle,
                provenance={
                    "fixture": "synthetic_environment_v1",
                    "configuration": {},
                    "unexpected": True,
                },
            )

    result = execute_planned_run(
        contract=contract,
        planned=planned,
        contract_sha256=sha256_file(CONTRACT_PATH),
        registry=_registry(fixture=extra_provenance_fixture()),
        harness_version="0.1.0",
        now=_clock(),
        run_id_factory=lambda: RUN_ID,
    )

    assert result.record["execution"]["validity"] == "invalid"
    assert result.record["execution"]["failure"]["kind"] == "orchestration_error"
    assert "unexpected field(s): unexpected" in result.record["execution"]["failure"]["message"]
    _validate_result(contract, result)
