from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml

from acmm.analysis.campaign_export import export_campaign
from acmm.analysis.export import analysis_export_error
from acmm.campaign.adjudication import create_campaign_adjudication
from acmm.campaign.execution import (
    create_campaign_execution_context,
    finalize_campaign_execution,
    frozen_campaign_plan,
    initialize_campaign_execution,
)
from acmm.campaign.merge import create_campaign_merge
from acmm.campaign.model import create_campaign
from acmm.runtime.experiment_execution import execute_experiment
from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    evaluation_context,
    execution_failure,
    judgment_result,
)
from acmm.runtime.registry import execution_registry
from experiment_package.support import (
    PACKAGE_SMOKE_CONTRACT,
    SMOKE_RUNTIME_SOURCE,
    install_test_package,
)


class _adapter:
    def execute(self, subject, scenario, environment, termination):
        del scenario
        del environment
        del termination
        return adapter_execution_result(
            termination="agent_completed",
            evidence=(
                captured_evidence(
                    id="final_response",
                    channel="final_response",
                    artifact="evidence/final_response.txt",
                    content=b"ok",
                ),
            ),
            subject_provenance={
                "agent": dict(subject["agent"]),
                "model": dict(subject["model"]),
                "configuration": dict(subject.get("configuration", {})),
            },
        )


class _retry_adapter(_adapter):
    def __init__(self) -> None:
        self.call_count = 0

    def execute(self, subject, scenario, environment, termination):
        self.call_count += 1
        if self.call_count == 1:
            return adapter_execution_result(
                termination="harness_error",
                evidence=(),
                subject_provenance=None,
                failure=execution_failure(
                    kind="adapter_connection_error",
                    message="simulated first-attempt failure",
                ),
            )
        return super().execute(subject, scenario, environment, termination)


class _tool_adapter(_adapter):
    def execute(self, subject, scenario, environment, termination):
        del scenario
        del environment
        del termination
        calls = [
            {
                "id": "call-1",
                "name": "read_item",
                "input": {"path": "a.txt"},
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "id": "call-2",
                "name": "write_item",
                "transport_name": "bridge",
                "input": {"path": "b.txt", "value": 2},
                "timestamp": "2026-01-01T00:00:01Z",
            },
        ]
        results = [
            {
                "id": "call-1",
                "name": "read_item",
                "output": "ok",
                "is_error": False,
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "id": "call-2",
                "name": "write_item",
                "transport_name": "bridge",
                "output": "rejected",
                "is_error": True,
                "timestamp": "2026-01-01T00:00:01Z",
            },
        ]
        return adapter_execution_result(
            termination="agent_completed",
            evidence=(
                captured_evidence(
                    id="tool_calls",
                    channel="tool_calls",
                    artifact="evidence/tool_calls.json",
                    content=json.dumps(calls, sort_keys=True).encode("utf-8"),
                ),
                captured_evidence(
                    id="tool_results",
                    channel="tool_results",
                    artifact="evidence/tool_results.json",
                    content=json.dumps(results, sort_keys=True).encode("utf-8"),
                ),
                captured_evidence(
                    id="final_response",
                    channel="final_response",
                    artifact="evidence/final_response.txt",
                    content=b"ok",
                ),
            ),
            subject_provenance={
                "agent": dict(subject["agent"]),
                "model": dict(subject["model"]),
                "configuration": dict(subject.get("configuration", {})),
            },
        )


class _compliant:
    def evaluate(self, context: evaluation_context) -> judgment_result:
        del context
        return judgment_result(
            outcome="compliant",
            evidence_ids=("final_response",),
            detail="acquisition judgment",
        )


def _registry(contract) -> execution_registry:
    return execution_registry(
        subject_adapters={subject["id"]: _adapter() for subject in contract["subjects"]},
        evaluators={"always_compliant": _compliant()},
    )


def _execute_runs(
    artifact,
    package,
    runs,
    execution_id: str,
    *,
    registry: execution_registry | None = None,
    max_attempts_per_run: int = 1,
):
    context = create_campaign_execution_context(
        artifact,
        tuple(runs),
        execution_id=execution_id,
    )
    journal = [None]

    def session_ready(session_dir: Path) -> None:
        assert session_dir == context.session_dir
        journal[0] = initialize_campaign_execution(context)

    def event_sink(event: str, payload) -> None:
        assert journal[0] is not None
        journal[0].emit(event, payload)

    result = execute_experiment(
        contract=dict(package.contract),
        contract_path=artifact.path / "contract.yaml",
        registry=registry if registry is not None else _registry(package.contract),
        results_root=artifact.path,
        harness_version="test-harness",
        max_attempts_per_run=max_attempts_per_run,
        run_indices=tuple(run.run_index for run in runs),
        session_parent=context.session_parent,
        execution_id_factory=lambda: context.execution_id,
        session_ready=session_ready,
        event_sink=event_sink,
        sleep=lambda seconds: None,
        package_provenance={
            "schema_version": package.schema_version,
            "sha256": package.package_sha256,
            "contract_sha256": package.contract_sha256,
            "runtime_sha256": package.runtime.sha256 if package.runtime is not None else None,
        },
    )
    finalize_campaign_execution(context, result, status="completed")
    return context


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def test_campaign_export_combines_campaign_history_and_merge_population(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    runs = frozen_campaign_plan(artifact).runs
    _execute_runs(artifact, package, runs[:3], "execution-a")
    _execute_runs(artifact, package, runs[3:], "execution-b")
    merged = create_campaign_merge(artifact, merge_id="merge-a")

    result = export_campaign(
        artifact.path,
        merged.path,
        tmp_path / "exports",
    )

    assert result.planned_run_rows == len(runs)
    assert result.run_rows == len(runs)
    assert result.judgment_rows == len(runs)

    planned = _read_csv(result.planned_runs_path)
    exported_runs = _read_csv(result.runs_path)
    judgments = _read_csv(result.judgments_path)
    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))

    assert len(planned) == 6
    assert all(row["observed"] == "true" for row in planned)
    assert [row["source_execution_id"] for row in planned[:3]] == ["execution-a"] * 3
    assert [row["source_execution_id"] for row in planned[3:]] == ["execution-b"] * 3

    assert len(exported_runs) == 6
    assert all(row["selected"] == "true" for row in exported_runs)
    assert all(row["source_selected"] == "true" for row in exported_runs)
    assert all(row["judgment_source"] == "merge" for row in exported_runs)
    assert all(row["attempt"] == "1" for row in exported_runs)
    assert [row["source_execution_order"] for row in exported_runs[:3]] == ["1"] * 3
    assert [row["source_execution_order"] for row in exported_runs[3:]] == ["2"] * 3
    assert all(row["outcome"] == "compliant" for row in judgments)
    assert all(row["judgment_source"] == "merge" for row in judgments)

    assert manifest["schema_version"] == "0.3"
    assert manifest["source"]["kind"] == "campaign"
    assert manifest["source"]["campaign_id"] == "campaign-a"
    assert manifest["source"]["merge_id"] == "merge-a"
    assert manifest["source"]["merge_population"] == {
        "total": 6,
        "observed": 6,
        "pending": 0,
    }
    assert [entry["execution_id"] for entry in manifest["executions"]] == [
        "execution-a",
        "execution-b",
    ]


def test_campaign_export_uses_adjudicated_merge_judgments_without_mutating_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    acquisition, _ = install_test_package(
        tmp_path,
        monkeypatch,
        source_name="acquisition-source",
    )
    artifact = create_campaign(
        acquisition,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    runs = frozen_campaign_plan(artifact).runs
    _execute_runs(artifact, acquisition, runs, "execution-a")

    revised_runtime = '''from acmm.adjudication_api import experiment_adjudicator
from acmm.experiment_api import experiment_runtime, harness_capabilities, judgment_result


class revised_evaluator:
    def evaluate(self, context):
        del context
        return judgment_result(
            outcome="violation",
            evidence_ids=("final_response",),
            detail="revised judgment",
        )


def build_runtime(context):
    del context
    return experiment_runtime(
        capabilities=harness_capabilities(
            evaluators=frozenset({"always_compliant"}),
            fixtures=frozenset(),
            evidence_channels=frozenset({"final_response"}),
        ),
        evaluators={"always_compliant": revised_evaluator()},
    )


def build_adjudicator(context):
    del context
    return experiment_adjudicator(
        evaluators={"always_compliant": revised_evaluator()},
    )
'''
    revised, _ = install_test_package(
        tmp_path,
        monkeypatch,
        runtime_source=revised_runtime,
        source_name="revised-source",
        with_adjudication=True,
    )
    adjudication = create_campaign_adjudication(
        artifact,
        revised,
        adjudication_id="revision-a",
    )
    merged = create_campaign_merge(
        artifact,
        merge_id="merge-a",
        adjudication_id=adjudication.adjudication_id,
    )

    result = export_campaign(artifact.path, merged.path, tmp_path / "exports")
    judgments = _read_csv(result.judgments_path)
    source_records = sorted(artifact.path.glob("executions/*/runs/*/run_record.yaml"))

    assert all(row["outcome"] == "violation" for row in judgments)
    assert all(row["judgment_source"] == "merge" for row in judgments)
    assert all(
        yaml.safe_load(path.read_text(encoding="utf-8"))["judgments"][0]["outcome"]
        == "compliant"
        for path in source_records
    )


def test_campaign_export_preserves_retry_history_and_merge_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(package, tmp_path / "generated", campaign_id="campaign-a")
    runs = frozen_campaign_plan(artifact).runs
    adapter = _retry_adapter()
    registry = execution_registry(
        subject_adapters={subject["id"]: adapter for subject in package.contract["subjects"]},
        evaluators={"always_compliant": _compliant()},
    )
    _execute_runs(
        artifact,
        package,
        runs[:1],
        "execution-a",
        registry=registry,
        max_attempts_per_run=2,
    )
    merged = create_campaign_merge(artifact, merge_id="merge-a", allow_partial=True)

    result = export_campaign(artifact.path, merged.path, tmp_path / "exports")
    exported_runs = _read_csv(result.runs_path)
    judgments = _read_csv(result.judgments_path)

    assert len(exported_runs) == 2
    assert [row["attempt"] for row in exported_runs] == ["1", "2"]
    assert [row["source_attempt"] for row in exported_runs] == ["1", "2"]
    assert [row["source_selected"] for row in exported_runs] == ["false", "true"]
    assert [row["selected"] for row in exported_runs] == ["false", "true"]
    assert [row["validity"] for row in exported_runs] == ["invalid", "valid"]
    assert [row["judgment_source"] for row in exported_runs] == ["source", "merge"]
    assert len(judgments) == 1
    assert judgments[0]["attempt"] == "2"
    assert judgments[0]["selected"] == "true"
    assert judgments[0]["judgment_source"] == "merge"


def test_campaign_export_preserves_unattempted_slots_in_partial_merge(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(package, tmp_path / "generated", campaign_id="campaign-a")
    runs = frozen_campaign_plan(artifact).runs
    _execute_runs(artifact, package, runs[:2], "execution-a")
    merged = create_campaign_merge(artifact, merge_id="merge-a", allow_partial=True)

    result = export_campaign(artifact.path, merged.path, tmp_path / "exports")
    planned = _read_csv(result.planned_runs_path)
    exported_runs = _read_csv(result.runs_path)
    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))

    assert len(planned) == len(runs)
    assert sum(row["observed"] == "true" for row in planned) == 2
    assert all(row["accepted_run_id"] == "" for row in planned[2:])
    assert len(exported_runs) == 2
    assert manifest["source"]["merge_completeness"] == "partial"
    assert manifest["source"]["merge_population"] == {
        "total": len(runs),
        "observed": 2,
        "pending": len(runs) - 2,
    }


def test_campaign_export_normalizes_tool_trajectory_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract = yaml.safe_load(PACKAGE_SMOKE_CONTRACT.read_text(encoding="utf-8"))
    contract["evidence_policy"]["capture"] = [
        "tool_calls",
        "tool_results",
        "final_response",
    ]
    contract_path = tmp_path / "tool-contract.yaml"
    contract_path.write_text(
        yaml.safe_dump(contract, sort_keys=False),
        encoding="utf-8",
        newline="",
    )
    runtime_source = SMOKE_RUNTIME_SOURCE.replace(
        'frozenset({"final_response"})',
        'frozenset({"final_response", "tool_calls", "tool_results"})',
    )
    package, _ = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=contract_path,
        runtime_source=runtime_source,
    )
    artifact = create_campaign(package, tmp_path / "generated", campaign_id="campaign-a")
    runs = frozen_campaign_plan(artifact).runs
    adapter = _tool_adapter()
    registry = execution_registry(
        subject_adapters={subject["id"]: adapter for subject in package.contract["subjects"]},
        evaluators={"always_compliant": _compliant()},
    )
    _execute_runs(
        artifact,
        package,
        runs[:1],
        "execution-a",
        registry=registry,
    )
    merged = create_campaign_merge(artifact, merge_id="merge-a", allow_partial=True)

    result = export_campaign(artifact.path, merged.path, tmp_path / "exports")
    calls = _read_csv(result.tool_calls_path)
    results = _read_csv(result.tool_results_path)
    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))

    assert result.tool_call_rows == 2
    assert result.tool_result_rows == 2
    assert [row["call_index"] for row in calls] == ["1", "2"]
    assert [row["tool_name"] for row in calls] == ["read_item", "write_item"]
    assert json.loads(calls[1]["input_json"]) == {"path": "b.txt", "value": 2}
    assert calls[1]["transport_name"] == "bridge"
    assert all(row["selected"] == "true" for row in calls)
    assert [row["result_index"] for row in results] == ["1", "2"]
    assert [row["tool_call_id"] for row in results] == ["call-1", "call-2"]
    assert [row["is_error"] for row in results] == ["false", "true"]
    assert manifest["tables"]["tool_calls"]["rows"] == 2
    assert manifest["tables"]["tool_results"]["rows"] == 2



def test_campaign_export_is_byte_deterministic_across_output_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(package, tmp_path / "generated", campaign_id="campaign-a")
    runs = frozen_campaign_plan(artifact).runs
    _execute_runs(artifact, package, runs, "execution-a")
    merged = create_campaign_merge(artifact, merge_id="merge-a")

    first = export_campaign(artifact.path, merged.path, tmp_path / "exports-a")
    second = export_campaign(artifact.path, merged.path, tmp_path / "exports-b")

    assert first.planned_runs_path.read_bytes() == second.planned_runs_path.read_bytes()
    assert first.runs_path.read_bytes() == second.runs_path.read_bytes()
    assert first.judgments_path.read_bytes() == second.judgments_path.read_bytes()
    assert first.tool_calls_path.read_bytes() == second.tool_calls_path.read_bytes()
    assert first.tool_results_path.read_bytes() == second.tool_results_path.read_bytes()
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()


def test_campaign_export_rejects_merge_from_another_campaign(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    first = create_campaign(package, tmp_path / "generated", campaign_id="campaign-a")
    second = create_campaign(package, tmp_path / "generated", campaign_id="campaign-b")
    runs = frozen_campaign_plan(second).runs
    _execute_runs(second, package, runs, "execution-b")
    merged = create_campaign_merge(second, merge_id="merge-b")

    with pytest.raises(analysis_export_error, match="does not match the supplied campaign"):
        export_campaign(first.path, merged.path, tmp_path / "exports")

def test_campaign_export_reports_coarse_progress_phases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(package, tmp_path / "generated", campaign_id="campaign-a")
    runs = frozen_campaign_plan(artifact).runs
    _execute_runs(artifact, package, runs, "execution-a")
    merged = create_campaign_merge(artifact, merge_id="merge-a")
    messages: list[str] = []

    result = export_campaign(
        artifact.path,
        merged.path,
        tmp_path / "exports",
        progress=messages.append,
    )

    assert result.planned_run_rows == len(runs)
    assert messages == [
        "loading and validating Campaign and Merge metadata...",
        "validating frozen Contract snapshots...",
        f"loading and validating {len(runs)} merged observation(s)...",
        "loading Campaign execution and retry history...",
        (
            f"reconciling {len(runs)} attempt(s) across 1 Campaign Execution(s) "
            f"with {len(runs)} accepted observation(s)..."
        ),
        "building deterministic analysis tables...",
        "normalizing tool-call and tool-result trajectory evidence...",
        (
            f"publishing {len(runs)} planned-run, {len(runs)} attempt, "
            f"{len(runs)} judgment, 0 tool-call, and 0 tool-result row(s)..."
        ),
    ]
