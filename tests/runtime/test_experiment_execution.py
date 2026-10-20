from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest
import yaml

import acmm.runtime.experiment_execution as experiment_execution_module
from acmm.contract.loader import load_contract
from acmm.runtime.experiment_execution import execute_experiment
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
                "configuration": {
                    "context_window": 64000,
                },
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

    result = execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(adapter),
        results_root=tmp_path,
        harness_version="0.1.0",
        max_attempts_per_run=1,
        now=_clock(),
        execution_id_factory=lambda: EXECUTION_ID,
        run_id_factory=_run_id_factory(),
    )

    assert result.unresolved_run_indices == ()
    assert len(result.slots) == 5
    assert adapter.call_count == 5

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
            max_attempts_per_run=0,
        )

    assert list(tmp_path.iterdir()) == []
