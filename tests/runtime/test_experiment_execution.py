from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest
import yaml

import acmm.runtime.experiment_execution as experiment_execution_module
from acmm.contract.loader import load_contract
from acmm.runtime.experiment_execution import (
    execute_experiment,
    experiment_execution_failed,
    experiment_execution_interrupted,
)
from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    evaluation_context,
    execution_failure,
    judgment_result,
    prepared_environment,
    runtime_resource_admission,
    runtime_resource_governor_session,
    runtime_run_recovery_error,
    runtime_run_recovery_result,
)
from acmm.runtime.registry import execution_registry
from acmm.runtime.provider_pressure import (
    runtime_provider_pressure_gate_configuration,
    runtime_provider_pressure_identity,
    runtime_provider_pressure_observation,
    runtime_provider_pressure_pacing_event,
    runtime_provider_pressure_policy,
    runtime_provider_pressure_signal,
    runtime_provider_pressure_workload,
)
from acmm.runtime.result_store import result_write_error


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    ROOT
    / "tests"
    / "fixtures"
    / "contracts"
    / "valid"
    / "runtime_execution.yaml"
)

EXECUTION_ID = "10000000-0000-4000-8000-000000000001"


class fake_fixture:
    def prepare(
        self,
        scenario: dict,
        reset: dict,
    ) -> prepared_environment:
        return prepared_environment(
            handle={"fixture_value": 87},
            provenance={
                "fixture": "synthetic_environment_v1",
                "configuration": {},
            },
        )


class fake_evaluator:
    def evaluate(
        self,
        context: evaluation_context,
    ) -> judgment_result:
        if context.opportunity["id"] == "retrieve":
            evidence_ids = ("tool_calls", "tool_results")
        else:
            evidence_ids = (
                "tool_calls",
                "tool_results",
                "final_response",
            )

        return judgment_result(
            outcome="compliant",
            evidence_ids=evidence_ids,
        )


class sequence_adapter:
    def __init__(
        self,
        invalid_call_numbers: set[int] | None = None,
        termination: str = "agent_completed",
    ) -> None:
        self.invalid_call_numbers = invalid_call_numbers or set()
        self.termination = termination
        self.call_count = 0

    def execute(
        self,
        subject: dict,
        scenario: dict,
        environment: object,
        termination: dict,
    ) -> adapter_execution_result:
        self.call_count += 1

        if self.call_count in self.invalid_call_numbers:
            return adapter_execution_result(
                termination="harness_error",
                evidence=(),
                subject_provenance=None,
                failure=execution_failure(
                    kind="adapter_connection_error",
                    message="simulated adapter failure",
                ),
            )

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
                "configuration": dict(subject.get("configuration", {})),
            },
        )


def _registry(adapter: sequence_adapter) -> execution_registry:
    evaluator = fake_evaluator()

    return execution_registry(
        subject_adapters={
            "hermes_gpt_oss120b": adapter,
        },
        fixtures={
            "synthetic_environment_v1": fake_fixture(),
        },
        evaluators={
            "synthetic_retrieval": evaluator,
            "synthetic_addition": evaluator,
        },
        subject_configuration_overlays={
            "hermes_gpt_oss120b": {
                "llm_baseline_id": "gpt_oss_120b_cloud_v1",
                "llm_minimum_context_window": 64000,
                "llm_requested_context_window": 64000,
            }
        },
    )


def _clock():
    current = datetime(
        2026,
        10,
        15,
        16,
        0,
        0,
        tzinfo=timezone.utc,
    )

    def now() -> datetime:
        nonlocal current
        value = current
        current += timedelta(seconds=1)
        return value

    return now


def _run_id_factory():
    counter = 1

    def make() -> str:
        nonlocal counter
        value = str(uuid.UUID(int=counter))
        counter += 1
        return value

    return make


def test_execute_experiment_snapshots_contract_and_completes_plan(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()

    ready_sessions: list[Path] = []

    def session_ready(path: Path) -> None:
        assert (path / "contract.yaml").is_file()
        ready_sessions.append(path)

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(adapter),
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        max_attempts_per_run=1,
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
        session_ready=session_ready,
    )

    assert ready_sessions == [result.session_dir]
    assert result.unresolved_run_indices == ()
    assert len(result.slots) == 5
    assert adapter.call_count == 5
    assert result.contract_run_count == 5
    assert result.started_at == datetime(
        2026,
        10,
        15,
        16,
        0,
        0,
        tzinfo=timezone.utc,
    )
    assert result.ended_at == datetime(
        2026,
        10,
        15,
        16,
        0,
        11,
        tzinfo=timezone.utc,
    )

    assert (
        result.session_dir / "contract.yaml"
    ).read_bytes() == CONTRACT_PATH.read_bytes()

    manifest = yaml.safe_load(
        result.manifest_path.read_text(encoding="utf-8")
    )

    assert manifest["execution"]["contract_run_count"] == 5
    assert manifest["execution"]["selected_run_indices"] == [0, 1, 2, 3, 4]
    assert manifest["execution"]["planned_run_count"] == 5
    assert manifest["execution"]["resolved_run_count"] == 5
    assert manifest["execution"]["unresolved_run_indices"] == []
    assert manifest["execution"]["subject_llm_baselines"] == {
        "hermes_gpt_oss120b": "gpt_oss_120b_cloud_v1"
    }
    assert len(manifest["slots"]) == 5

    for slot in manifest["slots"]:
        assert len(slot["attempts"]) == 1
        assert slot["selected_run_id"] == slot["attempts"][0]["run_id"]
        assert slot["attempts"][0]["result_path"].startswith("runs/run_")

        run_dir = result.session_dir / slot["attempts"][0]["result_path"]
        assert (run_dir / "run_record.yaml").is_file()


def test_run_selection_executes_only_requested_slot(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    messages: list[str] = []

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(adapter),
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        max_attempts_per_run=1,
        run_indices=(0,),
        progress=messages.append,
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
    )

    assert adapter.call_count == 1
    assert len(result.slots) == 1
    assert result.slots[0].planned.run_index == 0
    assert result.unresolved_run_indices == ()

    manifest = yaml.safe_load(
        result.manifest_path.read_text(encoding="utf-8")
    )

    execution = manifest["execution"]
    assert execution["contract_run_count"] == 5
    assert execution["selected_run_indices"] == [0]
    assert execution["planned_run_count"] == 1
    assert execution["resolved_run_count"] == 1
    assert [slot["run_index"] for slot in manifest["slots"]] == [0]
    assert any("selected 1 of 5 planned run(s)" in item for item in messages)


def test_run_selection_rejects_unknown_index_without_side_effects(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)

    with pytest.raises(ValueError, match="Run index 99"):
        execute_experiment(
            contract=contract,
            contract_path=CONTRACT_PATH,
            registry=_registry(sequence_adapter()),
            results_root=tmp_path,
            harness_version="0.1.0",
        sleep=lambda _: None,
            run_indices=(99,),
            execution_id_factory=lambda: EXECUTION_ID,
        )

    assert list(tmp_path.iterdir()) == []


def test_invalid_attempt_is_preserved_and_then_retried(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter(invalid_call_numbers={1})

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(adapter),
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        max_attempts_per_run=2,
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
    )

    first_slot = result.slots[0]

    assert len(first_slot.attempts) == 2
    assert first_slot.attempts[0].validity == "invalid"
    assert first_slot.attempts[1].validity == "valid"
    assert first_slot.selected_run_id == first_slot.attempts[1].run_id

    assert all(
        len(slot.attempts) == 1
        for slot in result.slots[1:]
    )

    assert adapter.call_count == 6
    assert result.unresolved_run_indices == ()


def test_valid_timeout_is_not_retried(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter(termination="timeout")

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(adapter),
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        max_attempts_per_run=3,
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
    )

    assert adapter.call_count == 5
    assert all(
        len(slot.attempts) == 1
        and slot.attempts[0].termination == "timeout"
        and slot.attempts[0].validity == "valid"
        for slot in result.slots
    )


def test_exhausted_invalid_attempts_leave_slots_unresolved(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter(
        invalid_call_numbers=set(range(1, 100))
    )

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(adapter),
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        max_attempts_per_run=2,
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
    )

    assert adapter.call_count == 10
    assert result.unresolved_run_indices == (0, 1, 2, 3, 4)
    assert all(
        len(slot.attempts) == 2
        and slot.selected_run_id is None
        for slot in result.slots
    )

    manifest = yaml.safe_load(
        result.manifest_path.read_text(encoding="utf-8")
    )

    assert manifest["execution"]["resolved_run_count"] == 0
    assert manifest["execution"]["unresolved_run_indices"] == [0, 1, 2, 3, 4]


def test_inter_run_pacing_waits_between_planned_slots_and_is_manifested(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    waits: list[float] = []
    messages: list[str] = []

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(adapter),
        results_root=tmp_path,
        harness_version="0.1.0",
        max_attempts_per_run=1,
        progress=messages.append,
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
        sleep=waits.append,
    )

    assert result.unresolved_run_indices == ()
    assert waits == [2.0, 2.0, 2.0, 2.0]
    assert sum("Pacing: waiting 2s" in item for item in messages) == 4

    manifest = yaml.safe_load(
        result.manifest_path.read_text(encoding="utf-8")
    )
    assert manifest["execution"]["pacing"] == {
        "inter_run_delay_seconds": 2.0,
    }


def test_single_selected_slot_does_not_wait_for_pacing(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    waits: list[float] = []

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(sequence_adapter()),
        results_root=tmp_path,
        harness_version="0.1.0",
        run_indices=(0,),
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
        sleep=waits.append,
    )

    assert result.unresolved_run_indices == ()
    assert waits == []


def test_pacing_is_between_slots_not_between_retry_attempts(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter(invalid_call_numbers={1})
    waits: list[float] = []

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(adapter),
        results_root=tmp_path,
        harness_version="0.1.0",
        max_attempts_per_run=2,
        run_indices=(0, 1),
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
        sleep=waits.append,
    )

    assert result.unresolved_run_indices == ()
    assert len(result.slots[0].attempts) == 2
    assert len(result.slots[1].attempts) == 1
    assert waits == [2.0]


def test_persistence_failure_leaves_session_without_completion_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    real_persist = experiment_execution_module.persist_executed_run_to
    call_count = 0

    def fail_on_second_persist(result, destination):
        nonlocal call_count
        call_count += 1

        if call_count == 2:
            raise result_write_error("simulated persistence failure")

        return real_persist(result, destination)

    monkeypatch.setattr(
        experiment_execution_module,
        "persist_executed_run_to",
        fail_on_second_persist,
    )

    with pytest.raises(
        experiment_execution_failed,
        match="simulated persistence failure",
    ) as captured:
        execute_experiment(
            contract=contract,
            contract_path=CONTRACT_PATH,
            registry=_registry(adapter),
            results_root=tmp_path,
            harness_version="0.1.0",
        sleep=lambda _: None,
            now=_clock(),
            execution_id_factory=lambda: EXECUTION_ID,
            run_id_factory=_run_id_factory(),
        )

    session_dir = (
        tmp_path
        / "runtime_execution_fixture_v1"
        / f"execution_{EXECUTION_ID}"
    )

    assert captured.value.result.failure_path == (
        session_dir / "experiment_failure.yaml"
    )
    assert (session_dir / "contract.yaml").is_file()
    assert (session_dir / "experiment_failure.yaml").is_file()
    assert not (session_dir / "experiment_execution.yaml").exists()

    failure = yaml.safe_load(
        (session_dir / "experiment_failure.yaml").read_text(encoding="utf-8")
    )
    assert failure["execution"]["status"] == "failed"
    assert failure["execution"]["failed_run_index"] == 1
    assert failure["execution"]["failure_detail"] == "simulated persistence failure"

    persisted_runs = list((session_dir / "runs").glob("run_*"))
    assert len(persisted_runs) == 1


def test_max_attempts_must_be_positive(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)

    with pytest.raises(ValueError, match="max_attempts_per_run"):
        execute_experiment(
            contract=contract,
            contract_path=CONTRACT_PATH,
            registry=_registry(sequence_adapter()),
            results_root=tmp_path,
            harness_version="0.1.0",
        sleep=lambda _: None,
            max_attempts_per_run=0,
        )

    assert list(tmp_path.iterdir()) == []


def test_keyboard_interrupt_persists_partial_session_without_completion_manifest(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    real_execute = adapter.execute

    def interrupt_on_second_run(*args, **kwargs):
        if adapter.call_count == 1:
            raise KeyboardInterrupt

        return real_execute(*args, **kwargs)

    adapter.execute = interrupt_on_second_run  # type: ignore[method-assign]

    with pytest.raises(experiment_execution_interrupted) as captured:
        execute_experiment(
            contract=contract,
            contract_path=CONTRACT_PATH,
            registry=_registry(adapter),
            results_root=tmp_path,
            harness_version="0.1.0",
            sleep=lambda _: None,
            now=_clock(),
            execution_id_factory=lambda: EXECUTION_ID,
            run_id_factory=_run_id_factory(),
            package_provenance={
                "schema_version": "0.1",
                "sha256": "a" * 64,
                "contract_sha256": "b" * 64,
                "runtime_sha256": None,
            },
        )

    result = captured.value.result
    session_dir = result.session_dir

    assert result.resolved_run_indices == (0,)
    assert result.interrupted_run_index == 1
    assert result.interrupted_attempt == 1
    assert result.not_started_run_indices == (2, 3, 4)
    assert result.unresolved_run_indices == (1, 2, 3, 4)
    assert (session_dir / "contract.yaml").is_file()
    assert (session_dir / "experiment_interruption.yaml").is_file()
    assert not (session_dir / "experiment_execution.yaml").exists()

    manifest = yaml.safe_load(
        (session_dir / "experiment_interruption.yaml").read_text(
            encoding="utf-8"
        )
    )
    execution = manifest["execution"]
    assert execution["status"] == "interrupted"
    assert execution["interrupt_kind"] == "keyboard_interrupt"
    assert execution["selected_run_indices"] == [0, 1, 2, 3, 4]
    assert execution["resolved_run_indices"] == [0]
    assert execution["unresolved_run_indices"] == [1, 2, 3, 4]
    assert execution["not_started_run_indices"] == [2, 3, 4]
    assert execution["interrupted_run_index"] == 1
    assert execution["interrupted_attempt"] == 1
    assert execution["package"] == {
        "schema_version": "0.1",
        "sha256": "a" * 64,
        "contract_sha256": "b" * 64,
        "runtime_sha256": None,
    }
    assert len(manifest["slots"]) == 2
    assert len(manifest["slots"][0]["attempts"]) == 1
    assert manifest["slots"][1]["attempts"] == []



def test_keyboard_interrupt_during_completion_manifest_becomes_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    real_write = experiment_execution_module._write_yaml_atomically

    def interrupt_completion(path, data):
        if path.name == "experiment_execution.yaml":
            raise KeyboardInterrupt

        return real_write(path, data)

    monkeypatch.setattr(
        experiment_execution_module,
        "_write_yaml_atomically",
        interrupt_completion,
    )

    with pytest.raises(experiment_execution_interrupted) as captured:
        execute_experiment(
            contract=contract,
            contract_path=CONTRACT_PATH,
            registry=_registry(sequence_adapter()),
            results_root=tmp_path,
            harness_version="0.1.0",
            sleep=lambda _: None,
            now=_clock(),
            execution_id_factory=lambda: EXECUTION_ID,
            run_id_factory=_run_id_factory(),
        )

    result = captured.value.result
    assert result.resolved_run_indices == (0, 1, 2, 3, 4)
    assert result.unresolved_run_indices == ()
    assert result.interrupted_run_index is None
    assert result.interrupted_attempt is None
    assert result.interruption_path.is_file()
    assert not (result.session_dir / "experiment_execution.yaml").exists()


def test_execution_manifest_records_experiment_package_provenance(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(sequence_adapter()),
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        run_indices=(0,),
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
        package_provenance={
            "schema_version": "0.1",
            "sha256": "a" * 64,
            "contract_sha256": "b" * 64,
            "runtime_sha256": "c" * 64,
        },
    )

    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["execution"]["package"] == {
        "schema_version": "0.1",
        "sha256": "a" * 64,
        "contract_sha256": "b" * 64,
        "runtime_sha256": "c" * 64,
    }


def test_execution_manifest_records_deployment_reconciliation(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(sequence_adapter()),
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        run_indices=(0,),
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
        deployment_reconciliation={
            "permitted": True,
            "initial_state": "divergent",
            "performed": True,
            "subjects": ["hermes_gpt_oss120b"],
            "final_state": "ready",
        },
    )

    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["execution"]["deployment_reconciliation"] == {
        "permitted": True,
        "initial_state": "divergent",
        "performed": True,
        "subjects": ["hermes_gpt_oss120b"],
        "final_state": "ready",
    }


def test_execution_manifest_records_best_effort_provider_usage(
    tmp_path: Path,
) -> None:
    class fake_usage_monitor:
        def __init__(self) -> None:
            self.started = False

        def begin(self) -> None:
            self.started = True

        def finish(self):
            assert self.started
            return {
                "test_provider": {
                    "status": "available",
                    "request_count": 7,
                }
            }

    monitor = fake_usage_monitor()
    result = execute_experiment(
        contract=load_contract(CONTRACT_PATH),
        contract_path=CONTRACT_PATH,
        registry=_registry(sequence_adapter()),
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        run_indices=(0,),
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
        provider_usage_monitor=monitor,
    )

    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["execution"]["provider_usage"] == {
        "test_provider": {
            "status": "available",
            "request_count": 7,
        }
    }


def test_provider_usage_monitor_failure_does_not_invalidate_execution(
    tmp_path: Path,
) -> None:
    messages: list[str] = []

    class failing_usage_monitor:
        def begin(self) -> None:
            raise RuntimeError("telemetry offline")

        def finish(self):
            raise AssertionError("finish must not run after begin fails")

    result = execute_experiment(
        contract=load_contract(CONTRACT_PATH),
        contract_path=CONTRACT_PATH,
        registry=_registry(sequence_adapter()),
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        run_indices=(0,),
        progress=messages.append,
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
        provider_usage_monitor=failing_usage_monitor(),
    )

    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))
    assert "provider_usage" not in manifest["execution"]
    assert any("telemetry unavailable" in message for message in messages)


class fake_runtime_lifecycle:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def session_initialize(self, context):
        if context.progress is not None:
            context.progress("Pacing: synthetic session provider wait, idling ~3s.")
        self.events.append(("session_initialize", context.execution_id))
        return {"session": context.execution_id}

    def run_prepare(self, session_handle, context, scenario, reset):
        del reset
        if context.progress is not None:
            context.progress("Pacing: synthetic provider wait, idling ~5s.")
        self.events.append(
            (
                "run_prepare",
                session_handle["session"],
                context.run_index,
                context.attempt,
                scenario["id"],
            )
        )
        return prepared_environment(
            handle={"fixture_value": 87},
            provenance={
                "fixture": "synthetic_environment_v1",
                "configuration": {"lifecycle": True},
            },
        )

    def run_observe(self, session_handle, context, scenario, handle):
        from acmm.runtime.interfaces import observed_environment

        del scenario, handle
        self.events.append(
            ("run_observe", session_handle["session"], context.run_index)
        )
        return observed_environment()

    def run_finalize(
        self,
        session_handle,
        context,
        scenario,
        handle,
        reset,
        outcome,
    ):
        del scenario, handle, reset
        result_path = Path(outcome.result_path) if outcome.result_path else None
        self.events.append(
            (
                "run_finalize",
                session_handle["session"],
                context.run_index,
                outcome.persisted,
                bool(result_path and result_path.is_dir()),
            )
        )
        return {"persisted": outcome.persisted}

    def session_finalize(self, session_handle, context, outcome):
        self.events.append(
            (
                "session_finalize",
                session_handle["session"],
                context.execution_id,
                outcome.completed,
            )
        )
        return {"completed": outcome.completed}


def test_runtime_lifecycle_finalizes_run_only_after_persistence(tmp_path: Path) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    lifecycle = fake_runtime_lifecycle()
    registry = _registry(adapter)
    registry = execution_registry(
        subject_adapters=registry.subject_adapters,
        lifecycle=lifecycle,
        fixtures=registry.fixtures,
        evaluators=registry.evaluators,
        subject_configuration_overlays=registry.subject_configuration_overlays,
    )

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=registry,
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        max_attempts_per_run=1,
        run_indices=(0,),
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
    )

    assert [event[0] for event in lifecycle.events] == [
        "session_initialize",
        "run_prepare",
        "run_observe",
        "run_finalize",
        "session_finalize",
    ]
    assert lifecycle.events[3][3:] == (True, True)
    run_dir = result.slots[0].attempts[0].result_path
    finalization = yaml.safe_load(
        (run_dir / "environment_finalization.yaml").read_text(encoding="utf-8")
    )
    assert finalization == {"ok": True, "result": {"persisted": True}}



class failing_finalize_runtime_lifecycle(fake_runtime_lifecycle):
    def run_finalize(
        self,
        session_handle,
        context,
        scenario,
        handle,
        reset,
        outcome,
    ):
        del scenario, handle, reset
        result_path = Path(outcome.result_path) if outcome.result_path else None
        self.events.append(
            (
                "run_finalize",
                session_handle["session"],
                context.run_index,
                outcome.persisted,
                bool(result_path and result_path.is_dir()),
            )
        )
        raise RuntimeError("synthetic finalization failure")


def test_runtime_lifecycle_finalization_failure_is_persisted_and_session_closes(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    lifecycle = failing_finalize_runtime_lifecycle()
    base = _registry(adapter)
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )

    with pytest.raises(experiment_execution_failed) as captured:
        execute_experiment(
            contract=contract,
            contract_path=CONTRACT_PATH,
            registry=registry,
            results_root=tmp_path,
            harness_version="0.1.0",
            sleep=lambda _: None,
            max_attempts_per_run=1,
            run_indices=(0,),
            now=_clock(),
            execution_id_factory=lambda: EXECUTION_ID,
            run_id_factory=_run_id_factory(),
        )

    result = captured.value.result
    assert result.failed_run_index == 0
    assert result.failed_attempt == 1
    assert "synthetic finalization failure" in result.detail
    assert len(result.slots) == 1
    assert len(result.slots[0].attempts) == 1
    persisted = result.slots[0].attempts[0].result_path
    assert persisted.is_dir()
    assert (persisted / "run_record.yaml").is_file()
    assert result.failure_path.is_file()
    manifest = yaml.safe_load(result.failure_path.read_text(encoding="utf-8"))
    assert manifest["execution"]["status"] == "failed"
    assert manifest["execution"]["failed_run_index"] == 0
    assert manifest["execution"]["persisted_attempt_count"] == 1
    assert lifecycle.events[-1][0] == "session_finalize"
    assert lifecycle.events[-1][-1] is False


def test_runtime_lifecycle_can_report_progress_through_run_context(tmp_path: Path) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    lifecycle = fake_runtime_lifecycle()
    base = _registry(adapter)
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )
    messages: list[str] = []

    execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=registry,
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        max_attempts_per_run=1,
        run_indices=(0,),
        progress=messages.append,
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
    )

    assert "Pacing: synthetic session provider wait, idling ~3s." in messages
    assert "Pacing: synthetic provider wait, idling ~5s." in messages

def test_execute_experiment_emits_durable_lifecycle_events_and_supports_session_parent(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    events: list[tuple[str, dict[str, object]]] = []
    session_parent = tmp_path / "campaign-executions"

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(adapter),
        results_root=tmp_path / "unused-results-root",
        session_parent=session_parent,
        harness_version="0.1.0",
        sleep=lambda _: None,
        max_attempts_per_run=1,
        run_indices=(0,),
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
        event_sink=lambda event, payload: events.append((event, dict(payload))),
    )

    assert result.session_dir == session_parent / f"execution_{EXECUTION_ID}"
    assert [event for event, _ in events] == [
        "execution_started",
        "logical_run_started",
        "logical_run_prepared",
        "attempt_started",
        "environment_prepare_started",
        "environment_prepared",
        "subject_execution_started",
        "subject_execution_ended",
        "attempt_persisted",
        "attempt_finalized",
        "logical_run_closed",
        "execution_completed",
    ]


class fake_materialized_runtime_lifecycle(fake_runtime_lifecycle):
    def materialization_initialize(self, session_handle, context):
        self.events.append(
            (
                "materialization_initialize",
                session_handle["session"],
                context.key,
                context.selected_run_indices,
                tuple(
                    (run.run_index, run.subject_id, run.scenario_id, run.repetition)
                    for run in context.runs
                ),
            )
        )
        return {"materialization": context.key}

    def materialization_finalize(self, session_handle, context, handle, outcome):
        self.events.append(
            (
                "materialization_finalize",
                session_handle["session"],
                context.key,
                handle["materialization"],
                outcome.completed,
                outcome.interrupted,
            )
        )
        return {"materialization": context.key, "completed": outcome.completed}

    def run_prepare(self, session_handle, context, scenario, reset):
        assert context.materialization_key == f"scenario_id={scenario['id']}"
        assert context.materialization_handle == {
            "materialization": context.materialization_key
        }
        return super().run_prepare(session_handle, context, scenario, reset)


class recoverable_finalize_materialized_lifecycle(fake_runtime_lifecycle):
    def __init__(self) -> None:
        super().__init__()
        self.generation = 0

    def materialization_initialize(self, session_handle, context):
        del session_handle
        self.generation += 1
        self.events.append(("materialization_initialize", context.key, self.generation))
        return {"generation": self.generation}

    def materialization_finalize(self, session_handle, context, handle, outcome):
        del session_handle
        generation = handle["generation"] if isinstance(handle, dict) else None
        self.events.append(
            (
                "materialization_finalize",
                context.key,
                generation,
                outcome.completed,
            )
        )
        return {"retired_generation": generation}

    def run_prepare(self, session_handle, context, scenario, reset):
        assert context.materialization_handle == {"generation": self.generation}
        return super().run_prepare(session_handle, context, scenario, reset)

    def run_finalize(
        self,
        session_handle,
        context,
        scenario,
        handle,
        reset,
        outcome,
    ):
        base = super().run_finalize(
            session_handle, context, scenario, handle, reset, outcome
        )
        if context.run_index == 0:
            return {
                **base,
                "restored": False,
                "retired": True,
                "continuation_safe": True,
                "materialization_reusable": False,
                "retryable": False,
                "recovery_detail": "synthetic post-run restoration timeout",
            }
        return {**base, "restored": True}


def test_valid_run_survives_recoverable_finalization_and_next_run_rematerializes(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    lifecycle = recoverable_finalize_materialized_lifecycle()
    base = _registry(adapter)
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        materialization_group_by=("scenario_id",),
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=registry,
        results_root=tmp_path,
        harness_version="test",
        sleep=lambda _: None,
        max_attempts_per_run=1,
        run_indices=(0, 1),
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
    )

    assert [slot.is_resolved for slot in result.slots] == [True, True]
    assert lifecycle.generation == 2
    finalized = [event for event in lifecycle.events if event[0] == "materialization_finalize"]
    assert finalized == [
        ("materialization_finalize", "scenario_id=synthetic_plus_13", 1, False),
        ("materialization_finalize", "scenario_id=synthetic_plus_13", 2, True),
    ]
    recovery_files = list((result.session_dir / "run_recovery").glob("*.yaml"))
    assert len(recovery_files) == 1
    recovery = yaml.safe_load(recovery_files[0].read_text(encoding="utf-8"))
    assert recovery["run_index"] == 0
    assert recovery["stage"] == "run_finalize"
    assert recovery["safe_to_continue"] is True
    assert recovery["materialization_reusable"] is False


class retry_rematerialization_lifecycle(fake_runtime_lifecycle):
    def __init__(self) -> None:
        super().__init__()
        self.generation = 0
        self.prepare_generations: list[tuple[int, int, int]] = []

    def materialization_initialize(self, session_handle, context):
        del session_handle
        self.generation += 1
        self.events.append(("materialization_initialize", context.key, self.generation))
        return {"generation": self.generation}

    def materialization_finalize(self, session_handle, context, handle, outcome):
        del session_handle
        generation = handle["generation"]
        self.events.append(
            (
                "materialization_finalize",
                context.key,
                generation,
                outcome.completed,
                outcome.detail,
            )
        )
        return {"generation": generation}

    def run_prepare(self, session_handle, context, scenario, reset):
        self.prepare_generations.append(
            (context.run_index, context.attempt, context.materialization_handle["generation"])
        )
        return super().run_prepare(session_handle, context, scenario, reset)


def test_invalid_attempt_retry_uses_fresh_materialization_generation(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    lifecycle = retry_rematerialization_lifecycle()
    base = _registry(sequence_adapter(invalid_call_numbers={1}))
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        materialization_group_by=("scenario_id",),
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )
    messages: list[str] = []

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=registry,
        results_root=tmp_path,
        harness_version="test",
        sleep=lambda _: None,
        max_attempts_per_run=2,
        run_indices=(0,),
        progress=messages.append,
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
    )

    assert result.slots[0].is_resolved is True
    assert len(result.slots[0].attempts) == 2
    assert lifecycle.prepare_generations == [(0, 1, 1), (0, 2, 2)]
    finalized = [event for event in lifecycle.events if event[0] == "materialization_finalize"]
    assert finalized[0][2:4] == (1, False)
    assert "retry_after_invalid_attempt" in str(finalized[0][4])
    assert finalized[1][2:4] == (2, True)
    assert any("starting the next attempt from a fresh materialization" in item for item in messages)


class recoverable_prepare_materialized_lifecycle(recoverable_finalize_materialized_lifecycle):
    def run_prepare(self, session_handle, context, scenario, reset):
        if context.run_index == 0:
            raise runtime_run_recovery_error(
                runtime_run_recovery_result(
                    safe_to_continue=True,
                    materialization_reusable=False,
                    retryable=False,
                    detail="synthetic preparation cleanup retired the materialization",
                    observations={"retired": True},
                )
            )
        return fake_runtime_lifecycle.run_prepare(
            self, session_handle, context, scenario, reset
        )

    def run_finalize(
        self,
        session_handle,
        context,
        scenario,
        handle,
        reset,
        outcome,
    ):
        return fake_runtime_lifecycle.run_finalize(
            self, session_handle, context, scenario, handle, reset, outcome
        )


def test_recoverable_prepare_failure_discards_slot_and_next_run_rematerializes(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    lifecycle = recoverable_prepare_materialized_lifecycle()
    base = _registry(sequence_adapter())
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        materialization_group_by=("scenario_id",),
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=registry,
        results_root=tmp_path,
        harness_version="test",
        sleep=lambda _: None,
        max_attempts_per_run=1,
        run_indices=(0, 1),
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
    )

    assert [slot.is_resolved for slot in result.slots] == [False, True]
    assert result.slots[0].attempts == ()
    assert lifecycle.generation == 2
    recovery_files = list((result.session_dir / "run_recovery").glob("*.yaml"))
    assert len(recovery_files) == 1
    recovery = yaml.safe_load(recovery_files[0].read_text(encoding="utf-8"))
    assert recovery["run_index"] == 0
    assert recovery["attempt"] == 1
    assert recovery["stage"] == "run_prepare"


class recoverable_materialization_initialize_lifecycle(
    recoverable_finalize_materialized_lifecycle
):
    def materialization_initialize(self, session_handle, context):
        del session_handle
        self.generation += 1
        if self.generation == 1:
            raise runtime_run_recovery_error(
                runtime_run_recovery_result(
                    safe_to_continue=True,
                    materialization_reusable=False,
                    retryable=False,
                    detail="synthetic failed materialization was retired",
                    observations={"retired": True},
                )
            )
        self.events.append(("materialization_initialize", context.key, self.generation))
        return {"generation": self.generation}

    def run_finalize(
        self,
        session_handle,
        context,
        scenario,
        handle,
        reset,
        outcome,
    ):
        return fake_runtime_lifecycle.run_finalize(
            self, session_handle, context, scenario, handle, reset, outcome
        )


def test_recoverable_materialization_initialize_failure_skips_only_current_slot(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    lifecycle = recoverable_materialization_initialize_lifecycle()
    base = _registry(sequence_adapter())
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        materialization_group_by=("scenario_id",),
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=registry,
        results_root=tmp_path,
        harness_version="test",
        sleep=lambda _: None,
        max_attempts_per_run=1,
        run_indices=(0, 1),
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
    )

    assert [slot.is_resolved for slot in result.slots] == [False, True]
    assert lifecycle.generation == 2
    recovery_files = list((result.session_dir / "run_recovery").glob("*.yaml"))
    assert len(recovery_files) == 1
    recovery = yaml.safe_load(recovery_files[0].read_text(encoding="utf-8"))
    assert recovery["attempt"] == 0
    assert recovery["stage"] == "materialization_initialize"


def test_materialization_hooks_wrap_subject_major_slice(tmp_path: Path) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    lifecycle = fake_materialized_runtime_lifecycle()
    base = _registry(adapter)
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        materialization_group_by=("scenario_id",),
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )
    events: list[tuple[str, dict[str, object]]] = []

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=registry,
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        max_attempts_per_run=1,
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
        event_sink=lambda event, payload: events.append((event, dict(payload))),
    )

    assert len(result.slots) == 5
    assert [event[0] for event in lifecycle.events].count(
        "materialization_initialize"
    ) == 1
    assert [event[0] for event in lifecycle.events].count(
        "materialization_finalize"
    ) == 1
    initialize = next(
        event for event in lifecycle.events if event[0] == "materialization_initialize"
    )
    assert initialize[2] == "scenario_id=synthetic_plus_13"
    assert initialize[3] == (0, 1, 2, 3, 4)
    assert initialize[4] == (
        (0, "hermes_gpt_oss120b", "synthetic_plus_13", 1),
        (1, "hermes_gpt_oss120b", "synthetic_plus_13", 2),
        (2, "hermes_gpt_oss120b", "synthetic_plus_13", 3),
        (3, "hermes_gpt_oss120b", "synthetic_plus_13", 4),
        (4, "hermes_gpt_oss120b", "synthetic_plus_13", 5),
    )
    finalize = next(
        event for event in lifecycle.events if event[0] == "materialization_finalize"
    )
    assert finalize[4:] == (True, False)
    event_names = [event for event, _ in events]
    assert event_names.count("materialization_initialize_started") == 1
    assert event_names.count("materialization_initialized") == 1
    assert event_names.count("materialization_finalized") == 1


class fake_execution_resource_governor:
    def __init__(self) -> None:
        self.history_counts: list[int] = []
        self.events: list[tuple[object, ...]] = []

    def execution_initialize(self, context):
        self.history_counts.append(len(context.history_records))
        return runtime_resource_governor_session(
            handle={"execution_id": context.execution_id},
            record={"id": "synthetic_resource_governor", "history_count": len(context.history_records)},
        )

    def materialization_admit(self, session_handle, context):
        self.events.append(("admit", context.key, context.selected_run_indices))
        return runtime_resource_admission(
            handle={"key": context.key},
            record={"status": "admitted", "forecast": {"requests": 7}},
        )

    def subject_slice_admit(self, session_handle, context):
        self.events.append(
            (
                "subject_slice_admit",
                context.materialization_key,
                context.subject_id,
                context.selected_run_indices,
            )
        )
        return runtime_resource_admission(
            handle={"subject_id": context.subject_id},
            record={"status": "admitted", "scope": "subject_slice"},
        )

    def materialization_finalize(self, session_handle, context, admission_handle, outcome):
        self.events.append(("finalize", context.key, outcome.completed, outcome.interrupted))
        return {
            "completed": outcome.completed,
            "interrupted": outcome.interrupted,
            "sample": {"requests": 5},
        }


def test_resource_governor_is_execution_level_and_reuses_prior_usage_history(tmp_path: Path) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    lifecycle = fake_materialized_runtime_lifecycle()
    governor = fake_execution_resource_governor()
    base = _registry(adapter)
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        resource_governor=governor,
        materialization_group_by=("scenario_id",),
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )

    first = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=registry,
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        max_attempts_per_run=1,
        now=_clock(),
        execution_id_factory=lambda: "20000000-0000-4000-8000-000000000001",
        run_id_factory=_run_id_factory(),
    )
    first_usage = yaml.safe_load(
        (first.session_dir / "execution_resource_usage.yaml").read_text(encoding="utf-8")
    )
    assert first_usage["execution"]["status"] == "completed"
    assert first_usage["governor"]["id"] == "synthetic_resource_governor"
    assert first_usage["governor"]["subject_slices"] == [
        {
            "materialization_key": "scenario_id=synthetic_plus_13",
            "subject_id": "hermes_gpt_oss120b",
            "selected_run_indices": [0, 1, 2, 3, 4],
            "admission": {"status": "admitted", "scope": "subject_slice"},
        }
    ]
    assert (
        "subject_slice_admit",
        "scenario_id=synthetic_plus_13",
        "hermes_gpt_oss120b",
        (0, 1, 2, 3, 4),
    ) in governor.events
    assert first_usage["history_artifacts"] == []
    assert first_usage["materializations"] == [
        {
            "key": "scenario_id=synthetic_plus_13",
            "selected_run_indices": [0, 1, 2, 3, 4],
            "admission": {"status": "admitted", "forecast": {"requests": 7}},
            "usage": {
                "completed": True,
                "interrupted": False,
                "sample": {"requests": 5},
            },
        }
    ]

    second = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=registry,
        results_root=tmp_path,
        harness_version="0.1.0",
        sleep=lambda _: None,
        max_attempts_per_run=1,
        now=_clock(),
        execution_id_factory=lambda: "20000000-0000-4000-8000-000000000002",
        run_id_factory=_run_id_factory(),
    )
    second_usage = yaml.safe_load(
        (second.session_dir / "execution_resource_usage.yaml").read_text(encoding="utf-8")
    )
    assert governor.history_counts == [0, 1]
    assert second_usage["history_artifacts"] == [
        str(first.session_dir / "execution_resource_usage.yaml")
    ]


class _execution_pressure_model:
    def identity(self, context):
        return runtime_provider_pressure_identity(
            provider_id="example",
            scope_id="scope",
            model_id="test_model",
            model_version="0.1",
            model_definition_sha256="c" * 64,
        )

    def policy(self, context):
        return runtime_provider_pressure_policy(
            punishment_increment=1.0,
            reward_decrement=0.1,
            base_delay_seconds=5.0,
            max_delay_seconds=60.0,
        )

    def historical_replay(self, context):
        return (
            runtime_provider_pressure_observation(
                source_kind="historical_replay",
                source_execution_id="old",
                signals=(
                    runtime_provider_pressure_signal(
                        kind="punishment",
                        count=1,
                        features={"operation.create": 1.0},
                    ),
                ),
            ),
        )

    def run_workload(self, context):
        return runtime_provider_pressure_workload(
            features={"operation.create": 1.0}
        )

    def run_measurement_start(self, context):
        return context.run_index

    def pacing_observe(self, context, event):
        return runtime_provider_pressure_observation(
            source_kind="live_execution",
            source_execution_id=context.execution_id,
        )

    def run_observe(self, context, measurement_handle, outcome):
        assert measurement_handle == context.run_index
        return runtime_provider_pressure_observation(
            source_kind="live_execution",
            source_execution_id=context.execution_id,
            signals=(
                runtime_provider_pressure_signal(
                    kind="reward",
                    count=1,
                    features={"operation.create": 1.0},
                ),
            ),
        )


def test_execute_experiment_uses_pph_below_execution_entry_point(
    tmp_path: Path,
) -> None:
    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    registry = _registry(adapter)
    registry = execution_registry(
        subject_adapters=registry.subject_adapters,
        provider_pressure_model=_execution_pressure_model(),
        fixtures=registry.fixtures,
        evaluators=registry.evaluators,
        subject_configuration_overlays=registry.subject_configuration_overlays,
    )
    waits: list[float] = []
    generated = tmp_path / "generated"

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=registry,
        results_root=generated / "results",
        harness_version="test",
        run_indices=(0,),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
        now=_clock(),
        sleep=waits.append,
    )

    assert result.slots[0].is_resolved
    assert waits == []
    trajectory = list((generated / "provider_pressure").rglob("*.yaml"))
    names = {path.name for path in trajectory}
    assert "profile-state.yaml" in names
    assert "trajectory-index.yaml" in names
    snapshots = [path for path in trajectory if path.parent.name == "trajectory"]
    assert len(snapshots) == 3


def test_provider_pressure_generated_root_is_shared_between_run_and_campaign(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "generated"
    assert experiment_execution_module._infer_generated_root(
        generated / "results",
        None,
    ) == generated.resolve()
    assert experiment_execution_module._infer_generated_root(
        generated / "ignored-results-root",
        generated / "github_mcp_project_workspace_v1" / "campaign_demo" / "executions",
    ) == generated.resolve()


def test_execute_experiment_exposes_pph_action_and_runtime_pacing_to_lifecycle(
    tmp_path: Path,
) -> None:
    class action_pressure_model(_execution_pressure_model):
        def historical_replay(self, context):
            return (
                runtime_provider_pressure_observation(
                    source_kind="historical_replay",
                    source_execution_id="old-action",
                    signals=(
                        runtime_provider_pressure_signal(
                            kind="punishment",
                            count=1,
                            features={"operation.repository.create": 1.0},
                        ),
                    ),
                ),
            )

        def run_workload(self, context):
            return runtime_provider_pressure_workload(features={})

        def pacing_observe(self, context, event):
            return runtime_provider_pressure_observation(
                source_kind="live_execution",
                source_execution_id=context.execution_id,
                signals=(
                    runtime_provider_pressure_signal(
                        kind="punishment",
                        count=1,
                        pressure_duration_seconds=event.duration_seconds,
                        features=event.features,
                    ),
                ) if event.reason == "provider_secondary_pressure" else (),
            )

        def recovery_observe(self, context, event):
            return runtime_provider_pressure_observation(
                source_kind="live_execution",
                source_execution_id=context.execution_id,
                signals=(
                    runtime_provider_pressure_signal(
                        kind="punishment",
                        count=1,
                        pressure_duration_seconds=event.underprediction_seconds,
                        features=event.features,
                    ),
                ) if event.underprediction_seconds > 0 else (),
            )

    class action_lifecycle(fake_runtime_lifecycle):
        def run_prepare(self, session_handle, context, scenario, reset):
            assert context.pressure_action is not None
            assert context.pressure_pacing is not None
            assert context.pressure_recovery is not None
            assert context.pressure_action_result is not None
            record = context.pressure_action(
                "repository.create",
                {"operation.repository.create": 1.0},
                {"test": True},
            )
            self.events.append(("action", record["action_id"], record["reason"]))
            feedback = context.pressure_pacing(
                "provider_secondary_pressure",
                60.0,
                "repository.create",
                {"operation.repository.create": 1.0},
                {"test": True},
            )
            self.events.append(("pacing", feedback["reason"], feedback["signal_count"]))
            recovery = context.pressure_recovery(
                "secondary",
                20.0,
                "retry_after",
                "repository.create",
                str(record["decision_id"]),
                {"operation.repository.create": 1.0},
                {"test": True, "recovery_attempt": 1},
            )
            self.events.append(
                (
                    "recovery",
                    recovery["underprediction_seconds"],
                    recovery["signal_count"],
                )
            )
            action_result = context.pressure_action_result(
                "success",
                "repository.create",
                str(record["decision_id"]),
                {"operation.repository.create": 1.0},
                {"test": True},
            )
            self.events.append(
                ("action_result", action_result["outcome"], action_result["decision_id"])
            )
            return super().run_prepare(session_handle, context, scenario, reset)

    contract = load_contract(CONTRACT_PATH)
    adapter = sequence_adapter()
    lifecycle = action_lifecycle()
    base = _registry(adapter)
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        provider_pressure_model=action_pressure_model(),
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )
    waits: list[float] = []

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=registry,
        results_root=tmp_path / "generated" / "results",
        harness_version="test",
        run_indices=(0,),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
        now=_clock(),
        sleep=waits.append,
    )

    assert result.slots[0].is_resolved
    assert 5.0 in waits
    assert 20.0 in waits
    assert any(event[:2] == ("action", "repository.create") for event in lifecycle.events)
    assert any(event[:2] == ("pacing", "provider_secondary_pressure") for event in lifecycle.events)
    assert any(event[:2] == ("recovery", 15.0) for event in lifecycle.events)
    assert any(event[:2] == ("action_result", "success") for event in lifecycle.events)



def test_keyboard_interrupt_during_deferred_run_finalization_records_interrupted_marker(
    tmp_path: Path,
) -> None:
    class interrupting_finalize_lifecycle(fake_runtime_lifecycle):
        def run_finalize(
            self,
            session_handle,
            context,
            scenario,
            handle,
            reset,
            outcome,
        ):
            del scenario, handle, reset
            self.events.append(
                ("run_finalize_interrupted", context.run_index, outcome.persisted)
            )
            raise KeyboardInterrupt

    contract = load_contract(CONTRACT_PATH)
    base = _registry(sequence_adapter())
    lifecycle = interrupting_finalize_lifecycle()
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )

    with pytest.raises(experiment_execution_interrupted) as captured:
        execute_experiment(
            contract=contract,
            contract_path=CONTRACT_PATH,
            registry=registry,
            results_root=tmp_path,
            harness_version="test",
            run_indices=(0,),
            now=_clock(),
            execution_id_factory=lambda: EXECUTION_ID,
            run_id_factory=_run_id_factory(),
        )

    result = captured.value.result
    assert result.interruption_path.is_file()
    finalization_markers = list(tmp_path.rglob("environment_finalization.yaml"))
    assert len(finalization_markers) == 1
    marker = yaml.safe_load(
        finalization_markers[0].read_text(encoding="utf-8")
    )
    assert marker["ok"] is False
    assert marker["interrupted"] is True
    assert marker["detail"] == "KeyboardInterrupt"


def test_keyboard_interrupt_during_run_persistence_finalizes_unpersisted_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class recording_lifecycle(fake_runtime_lifecycle):
        def run_finalize(
            self,
            session_handle,
            context,
            scenario,
            handle,
            reset,
            outcome,
        ):
            del session_handle, scenario, handle, reset
            self.events.append(
                (
                    "run_finalize_after_persist_interrupt",
                    context.run_index,
                    outcome.persisted,
                    outcome.interrupted,
                )
            )
            return {"restored": True}

    def interrupt_persist(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        experiment_execution_module,
        "persist_executed_run_to",
        interrupt_persist,
    )
    contract = load_contract(CONTRACT_PATH)
    base = _registry(sequence_adapter())
    lifecycle = recording_lifecycle()
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )

    with pytest.raises(experiment_execution_interrupted):
        execute_experiment(
            contract=contract,
            contract_path=CONTRACT_PATH,
            registry=registry,
            results_root=tmp_path,
            harness_version="test",
            run_indices=(0,),
            now=_clock(),
            execution_id_factory=lambda: EXECUTION_ID,
            run_id_factory=_run_id_factory(),
        )

    assert (
        "run_finalize_after_persist_interrupt",
        0,
        False,
        True,
    ) in lifecycle.events

def test_keyboard_interrupt_during_session_initialize_persists_typed_boundary(
    tmp_path: Path,
) -> None:
    class interrupting_session_lifecycle(fake_runtime_lifecycle):
        def session_initialize(self, context):
            self.events.append(("session_initialize_interrupted", context.execution_id))
            raise KeyboardInterrupt

    contract = load_contract(CONTRACT_PATH)
    base = _registry(sequence_adapter())
    lifecycle = interrupting_session_lifecycle()
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )

    with pytest.raises(experiment_execution_interrupted) as captured:
        execute_experiment(
            contract=contract,
            contract_path=CONTRACT_PATH,
            registry=registry,
            results_root=tmp_path,
            harness_version="test",
            run_indices=(0,),
            now=_clock(),
            execution_id_factory=lambda: EXECUTION_ID,
            run_id_factory=_run_id_factory(),
        )

    result = captured.value.result
    assert result.slots == ()
    assert result.interruption_path.is_file()
    manifest = yaml.safe_load(result.interruption_path.read_text(encoding="utf-8"))
    assert manifest["execution"]["status"] == "interrupted"
    assert manifest["execution"]["resolved_run_indices"] == []
    assert manifest["execution"]["not_started_run_indices"] == [0]


def test_keyboard_interrupt_during_materialization_initialize_finalizes_slice_boundary(
    tmp_path: Path,
) -> None:
    class interrupting_materialization_lifecycle(fake_runtime_lifecycle):
        def materialization_initialize(self, session_handle, context):
            self.events.append(("materialization_initialize", context.key))
            raise KeyboardInterrupt

        def materialization_finalize(self, session_handle, context, handle, outcome):
            self.events.append(
                (
                    "materialization_finalize",
                    context.key,
                    handle,
                    outcome.completed,
                    outcome.interrupted,
                )
            )
            return {"retired": True}

    contract = load_contract(CONTRACT_PATH)
    base = _registry(sequence_adapter())
    lifecycle = interrupting_materialization_lifecycle()
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        materialization_group_by=("scenario_id",),
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )

    with pytest.raises(experiment_execution_interrupted):
        execute_experiment(
            contract=contract,
            contract_path=CONTRACT_PATH,
            registry=registry,
            results_root=tmp_path,
            harness_version="test",
            run_indices=(0,),
            now=_clock(),
            execution_id_factory=lambda: EXECUTION_ID,
            run_id_factory=_run_id_factory(),
        )

    assert ("materialization_initialize", "scenario_id=synthetic_plus_13") in lifecycle.events
    assert (
        "materialization_finalize",
        "scenario_id=synthetic_plus_13",
        None,
        False,
        True,
    ) in lifecycle.events
    assert lifecycle.events[-1][0] == "session_finalize"


def test_keyboard_interrupt_quiesces_gate_before_provider_facing_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ordering: list[str] = []

    class recording_gate:
        def start(self):
            ordering.append("gate_start")

        def activate(self, context):
            del context
            ordering.append("gate_activate")

        def quiesce(self, detail):
            assert detail == "keyboard_interrupt"
            ordering.append("gate_quiesce")
            return 1

        def deactivate(self):
            ordering.append("gate_deactivate")

        def close(self):
            ordering.append("gate_close")

    gate = recording_gate()
    monkeypatch.setattr(
        experiment_execution_module,
        "provider_pressure_transport_gate",
        lambda *args, **kwargs: gate,
    )

    class gated_pressure_model(_execution_pressure_model):
        def transport_gate_configuration(self, context):
            return runtime_provider_pressure_gate_configuration(
                control_path=str(tmp_path / "provider-gate.json"),
                bind_host="127.0.0.1",
                bind_port=18765,
                advertised_url="http://127.0.0.1:18765",
            )

    class recording_lifecycle(fake_materialized_runtime_lifecycle):
        def materialization_finalize(self, session_handle, context, handle, outcome):
            ordering.append("materialization_finalize")
            return super().materialization_finalize(
                session_handle, context, handle, outcome
            )

        def session_finalize(self, session_handle, context, outcome):
            ordering.append("session_finalize")
            return super().session_finalize(session_handle, context, outcome)

    adapter = sequence_adapter()

    def interrupt_execute(*args, **kwargs):
        raise KeyboardInterrupt

    adapter.execute = interrupt_execute  # type: ignore[method-assign]
    base = _registry(adapter)
    lifecycle = recording_lifecycle()
    registry = execution_registry(
        subject_adapters=base.subject_adapters,
        lifecycle=lifecycle,
        provider_pressure_model=gated_pressure_model(),
        materialization_group_by=("scenario_id",),
        fixtures=base.fixtures,
        evaluators=base.evaluators,
        subject_configuration_overlays=base.subject_configuration_overlays,
    )

    with pytest.raises(experiment_execution_interrupted):
        execute_experiment(
            contract=load_contract(CONTRACT_PATH),
            contract_path=CONTRACT_PATH,
            registry=registry,
            results_root=tmp_path / "generated" / "results",
            harness_version="test",
            run_indices=(0,),
            now=_clock(),
            execution_id_factory=lambda: EXECUTION_ID,
            run_id_factory=_run_id_factory(),
        )

    assert ordering.index("gate_quiesce") < ordering.index("materialization_finalize")
    assert ordering.index("materialization_finalize") < ordering.index("session_finalize")
    assert ordering.index("session_finalize") < ordering.index("gate_deactivate")
    assert ordering.index("gate_deactivate") < ordering.index("gate_close")
