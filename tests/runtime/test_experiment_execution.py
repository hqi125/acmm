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
    experiment_execution_interrupted,
)
from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    evaluation_context,
    execution_failure,
    judgment_result,
    prepared_environment,
)
from acmm.runtime.registry import execution_registry
from acmm.runtime.result_store import result_write_error


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    ROOT
    / "experiments"
    / "examples"
    / "magic_number"
    / "experiment.yaml"
)

EXECUTION_ID = "10000000-0000-4000-8000-000000000001"


class fake_fixture:
    def prepare(
        self,
        scenario: dict,
        reset: dict,
    ) -> prepared_environment:
        return prepared_environment(
            handle={"magic_number": 87},
            provenance={
                "fixture": "magic_number_environment_v1",
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
                "configuration": dict(subject.get("configuration", {})),
            },
        )


def _registry(adapter: sequence_adapter) -> execution_registry:
    evaluator = fake_evaluator()

    return execution_registry(
        subject_adapters={
            "hermes_nemotron30b": adapter,
        },
        fixtures={
            "magic_number_environment_v1": fake_fixture(),
        },
        evaluators={
            "magic_number_retrieval": evaluator,
            "magic_number_addition": evaluator,
        },
        subject_configuration_overlays={
            "hermes_nemotron30b": {
                "llm_baseline_id": "nemotron_3_nano_30b_cloud_v1",
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
        "hermes_nemotron30b": "nemotron_3_nano_30b_cloud_v1"
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
        result_write_error,
        match="simulated persistence failure",
    ):
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
        / "magic_number_tool_use_v1"
        / f"execution_{EXECUTION_ID}"
    )

    assert (session_dir / "contract.yaml").is_file()
    assert not (session_dir / "experiment_execution.yaml").exists()

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
            "subjects": ["hermes_nemotron30b"],
            "final_state": "ready",
        },
    )

    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["execution"]["deployment_reconciliation"] == {
        "permitted": True,
        "initial_state": "divergent",
        "performed": True,
        "subjects": ["hermes_nemotron30b"],
        "final_state": "ready",
    }
