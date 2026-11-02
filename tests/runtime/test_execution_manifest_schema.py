from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from jsonschema import Draft202012Validator
import pytest
import yaml

from acmm.contract.loader import load_contract
from acmm.pilots.magic_number import build_magic_number_registry
from acmm.runtime.execution_manifest import (
    EXPERIMENT_EXECUTION_SCHEMA_VERSION,
    load_execution_manifest_schema,
)
from acmm.runtime.experiment_execution import (
    execute_experiment,
    experiment_execution_interrupted,
)
from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "experiments" / "examples" / "magic_number" / "experiment.yaml"
EXECUTION_ID = "10000000-0000-4000-8000-000000000001"


class sequence_adapter:
    def __init__(self) -> None:
        self.call_count = 0

    def execute(
        self,
        subject: dict,
        scenario: dict,
        environment: object,
        termination: dict,
    ) -> adapter_execution_result:
        self.call_count += 1
        return adapter_execution_result(
            termination="agent_completed",
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


def _registry(adapter: sequence_adapter):
    return build_magic_number_registry(
        {
            "hermes_nemotron30b": adapter,
        }
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


def test_execution_manifest_schema_is_versioned_and_valid() -> None:
    schema = load_execution_manifest_schema()

    assert EXPERIMENT_EXECUTION_SCHEMA_VERSION == "0.1"
    assert schema["title"] == "ACMM Experiment Execution Manifest v0.1"


def test_completed_execution_manifest_conforms_to_schema(tmp_path: Path) -> None:
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
        package_provenance={
            "schema_version": "0.1",
            "sha256": "a" * 64,
            "contract_sha256": "b" * 64,
            "runtime_sha256": None,
        },
        deployment_reconciliation={
            "permitted": True,
            "initial_state": "ready",
            "performed": False,
            "subjects": [],
            "final_state": "ready",
        },
    )
    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))

    Draft202012Validator(load_execution_manifest_schema()).validate(manifest)


def test_unknown_execution_manifest_schema_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported Experiment Execution"):
        load_execution_manifest_schema("9.9")


def test_interrupted_execution_manifest_conforms_to_schema(tmp_path: Path) -> None:
    adapter = sequence_adapter()
    real_execute = adapter.execute

    def interrupt_on_second_run(*args, **kwargs):
        if adapter.call_count == 1:
            raise KeyboardInterrupt
        return real_execute(*args, **kwargs)

    adapter.execute = interrupt_on_second_run  # type: ignore[method-assign]

    with pytest.raises(experiment_execution_interrupted) as captured:
        execute_experiment(
            contract=load_contract(CONTRACT_PATH),
            contract_path=CONTRACT_PATH,
            registry=_registry(adapter),
            results_root=tmp_path,
            harness_version="0.1.0",
            sleep=lambda _: None,
            now=_clock(),
            execution_id_factory=lambda: EXECUTION_ID,
            run_id_factory=_run_id_factory(),
        )

    manifest = yaml.safe_load(
        captured.value.result.interruption_path.read_text(encoding="utf-8")
    )
    Draft202012Validator(load_execution_manifest_schema()).validate(manifest)
