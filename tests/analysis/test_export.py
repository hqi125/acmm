from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from acmm.analysis.export import analysis_export_error, export_execution
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


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "runtime_execution.yaml"


class fake_fixture:
    def prepare(
        self,
        scenario: dict,
        reset: dict,
    ) -> prepared_environment:
        return prepared_environment(
            handle={"ACMM_FIXTURE_VALUE": "87"},
            provenance={
                "fixture": "synthetic_environment_v1",
                "configuration": {"fixture_value": 87},
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
            detail=f"judged {context.opportunity['id']}",
        )


class sequence_adapter:
    def __init__(
        self,
        invalid_call_numbers: set[int] | None = None,
    ) -> None:
        self.invalid_call_numbers = invalid_call_numbers or set()
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
                    content=b'[{"name":"get_fixture_value"}]',
                ),
                captured_evidence(
                    id="tool_results",
                    channel="tool_results",
                    artifact="evidence/tool_results.json",
                    content=b'[{"fixture_value":87}]',
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
                "configuration": subject.get("configuration", {}),
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
    )


def _clock():
    current = datetime(
        2026,
        10,
        20,
        20,
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


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _execute(
    tmp_path: Path,
    adapter: sequence_adapter,
    *,
    run_indices: tuple[int, ...] | None = None,
    max_attempts_per_run: int = 1,
):
    contract = load_contract(CONTRACT_PATH)

    return execute_experiment(
        contract=contract,
        contract_path=CONTRACT_PATH,
        registry=_registry(adapter),
        results_root=tmp_path / "results",
        harness_version="0.1.0",
        max_attempts_per_run=max_attempts_per_run,
        run_indices=run_indices,
        now=_clock(),
        sleep=lambda seconds: None,
    )


def test_export_complete_execution_produces_runs_and_judgments(
    tmp_path: Path,
) -> None:
    execution = _execute(tmp_path, sequence_adapter())

    result = export_execution(
        execution.session_dir,
        tmp_path / "exports",
    )

    assert result.run_rows == 5
    assert result.judgment_rows == 10

    runs = _read_csv(result.runs_path)
    judgments = _read_csv(result.judgments_path)

    assert [row["run_index"] for row in runs] == ["0", "1", "2", "3", "4"]
    assert all(row["attempt"] == "1" for row in runs)
    assert all(row["selected"] == "true" for row in runs)
    assert all(row["validity"] == "valid" for row in runs)
    assert all(row["termination"] == "agent_completed" for row in runs)
    assert all(row["execution_contract_run_count"] == "5" for row in runs)
    assert all(row["execution_selected_run_count"] == "5" for row in runs)
    assert all(row["inter_run_delay_seconds"] == "2.0" for row in runs)
    assert all(row["judgment_count"] == "2" for row in runs)
    assert all(row["agent_name"] == "Hermes Agent" for row in runs)
    assert all(row["environment_fixture"] == "synthetic_environment_v1" for row in runs)

    assert {row["opportunity_id"] for row in judgments} == {
        "retrieve",
        "calculate",
    }
    assert all(row["outcome"] == "compliant" for row in judgments)
    assert {row["dimension"] for row in judgments} == {
        "obligation",
        "procedure",
    }
    assert all(row["selected"] == "true" for row in judgments)

    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"]["execution_id"] == execution.execution_id
    assert manifest["tables"]["runs"]["rows"] == 5
    assert manifest["tables"]["judgments"]["rows"] == 10


def test_export_preserves_invalid_retry_attempts_without_judgments(
    tmp_path: Path,
) -> None:
    execution = _execute(
        tmp_path,
        sequence_adapter(invalid_call_numbers={1}),
        run_indices=(0,),
        max_attempts_per_run=2,
    )

    result = export_execution(
        execution.session_dir,
        tmp_path / "exports",
    )

    runs = _read_csv(result.runs_path)
    judgments = _read_csv(result.judgments_path)

    assert result.run_rows == 2
    assert result.judgment_rows == 2

    assert runs[0]["attempt"] == "1"
    assert runs[0]["selected"] == "false"
    assert runs[0]["validity"] == "invalid"
    assert runs[0]["termination"] == "harness_error"
    assert runs[0]["failure_kind"] == "adapter_connection_error"
    assert runs[0]["judgment_count"] == "0"

    assert runs[1]["attempt"] == "2"
    assert runs[1]["selected"] == "true"
    assert runs[1]["validity"] == "valid"
    assert all(row["attempt"] == "2" for row in judgments)
    assert all(row["selected"] == "true" for row in judgments)


def test_export_is_byte_deterministic_across_output_roots(
    tmp_path: Path,
) -> None:
    execution = _execute(
        tmp_path,
        sequence_adapter(),
        run_indices=(0,),
    )

    first = export_execution(execution.session_dir, tmp_path / "exports_a")
    second = export_execution(execution.session_dir, tmp_path / "exports_b")

    assert first.runs_path.read_bytes() == second.runs_path.read_bytes()
    assert first.judgments_path.read_bytes() == second.judgments_path.read_bytes()
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()


def test_export_refuses_to_overwrite_existing_export(
    tmp_path: Path,
) -> None:
    execution = _execute(
        tmp_path,
        sequence_adapter(),
        run_indices=(0,),
    )
    output_root = tmp_path / "exports"

    export_execution(execution.session_dir, output_root)

    with pytest.raises(FileExistsError, match="already exists"):
        export_execution(execution.session_dir, output_root)


def test_export_rejects_manifest_record_drift(
    tmp_path: Path,
) -> None:
    execution = _execute(
        tmp_path,
        sequence_adapter(),
        run_indices=(0,),
    )
    manifest = yaml.safe_load(execution.manifest_path.read_text(encoding="utf-8"))
    manifest["slots"][0]["attempts"][0]["termination"] = "timeout"
    execution.manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(
        analysis_export_error,
        match="attempt metadata does not match Run Record",
    ):
        export_execution(execution.session_dir, tmp_path / "exports")


def test_export_rejects_evidence_hash_drift(
    tmp_path: Path,
) -> None:
    execution = _execute(
        tmp_path,
        sequence_adapter(),
        run_indices=(0,),
    )
    run_dir = execution.slots[0].attempts[0].result_path
    evidence_path = run_dir / "evidence" / "final_response.txt"
    evidence_path.write_text("tampered", encoding="utf-8")

    with pytest.raises(
        analysis_export_error,
        match="Evidence artifact SHA-256 does not match Run Record",
    ):
        export_execution(execution.session_dir, tmp_path / "exports")
