from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import yaml

import pytest

from acmm.campaign.execution import (
    create_campaign_execution_context,
    finalize_campaign_execution,
    frozen_campaign_plan,
    initialize_campaign_execution,
    reconcile_campaign_executions,
)
from acmm.campaign.merge import create_campaign_merge, validate_campaign_merge_manifest
from acmm.campaign.model import campaign_error, create_campaign
from acmm.runtime.experiment_execution import execute_experiment
from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    evaluation_context,
    judgment_result,
)
from acmm.runtime.registry import execution_registry
from experiment_package.support import install_test_package


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


class _evaluator:
    def evaluate(self, context: evaluation_context) -> judgment_result:
        del context
        return judgment_result(
            outcome="compliant",
            evidence_ids=("final_response",),
            detail="campaign merge fixture",
        )


def _registry(contract) -> execution_registry:
    return execution_registry(
        subject_adapters={subject["id"]: _adapter() for subject in contract["subjects"]},
        evaluators={"always_compliant": _evaluator()},
    )


def _execute_campaign_runs(artifact, package, runs, execution_id: str):
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
        registry=_registry(package.contract),
        results_root=artifact.path,
        harness_version="test-harness",
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


def test_campaign_merge_requires_complete_campaign_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    run = frozen_campaign_plan(artifact).runs[0]
    _execute_campaign_runs(artifact, package, (run,), "execution-a")

    with pytest.raises(campaign_error, match="Complete campaign merge requires"):
        create_campaign_merge(artifact, merge_id="merge-a")


def test_partial_campaign_merge_copies_only_accepted_observations_with_provenance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    run = frozen_campaign_plan(artifact).runs[0]
    _execute_campaign_runs(artifact, package, (run,), "execution-a")

    merged = create_campaign_merge(
        artifact,
        allow_partial=True,
        merge_id="merge-a",
    )

    assert merged.manifest["merge"]["completeness"] == "partial"
    assert merged.manifest["population"]["observed"] == 1
    assert len(merged.manifest["observations"]) == 1
    observation = merged.manifest["observations"][0]
    assert observation["logical_run_id"] == artifact.manifest["runs"][0]["run_id"]
    assert observation["source_execution_id"] == "execution-a"
    merged_result = merged.path / observation["merged_result"]
    assert (merged_result / "run_record.yaml").is_file()
    assert (merged_result / "evidence" / "final_response.txt").read_bytes() == b"ok"
    assert (merged.path / "campaign.json").is_file()
    assert (merged.path / "contract.yaml").is_file()


def test_complete_campaign_merge_contains_every_logical_run(
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
    _execute_campaign_runs(artifact, package, runs, "execution-a")

    merged = create_campaign_merge(artifact, merge_id="merge-a")

    assert merged.manifest["merge"]["completeness"] == "complete"
    assert merged.manifest["population"] == {
        "total": len(runs),
        "observed": len(runs),
        "pending": 0,
    }
    assert len(merged.manifest["observations"]) == len(runs)


def test_campaign_merge_rejects_duplicate_accepted_observation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    run = frozen_campaign_plan(artifact).runs[0]
    context = _execute_campaign_runs(artifact, package, (run,), "execution-a")

    duplicate = context.session_parent / "execution_execution-b"
    shutil.copytree(context.session_dir, duplicate)
    manifest_path = duplicate / "campaign_execution.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["execution"]["id"] = "execution-b"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    legacy_path = duplicate / "experiment_execution.yaml"
    import yaml
    legacy = yaml.safe_load(legacy_path.read_text(encoding="utf-8"))
    legacy["execution"]["id"] = "execution-b"
    legacy_path.write_text(yaml.safe_dump(legacy, sort_keys=False), encoding="utf-8")

    with pytest.raises(campaign_error, match="multiple accepted observations"):
        create_campaign_merge(
            artifact,
            allow_partial=True,
            merge_id="merge-a",
        )

def test_partial_campaign_merge_with_no_observations_has_stable_runs_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )

    merged = create_campaign_merge(
        artifact,
        allow_partial=True,
        merge_id="merge-a",
    )

    assert merged.manifest["population"]["observed"] == 0
    assert merged.manifest["observations"] == []
    assert (merged.path / "runs").is_dir()


def test_campaign_merge_rejects_symbolic_link_source_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    run = frozen_campaign_plan(artifact).runs[0]
    context = _execute_campaign_runs(artifact, package, (run,), "execution-a")
    source_manifest = context.session_dir / "experiment_execution.yaml"
    real_manifest = context.session_dir / "experiment_execution.real.yaml"
    source_manifest.rename(real_manifest)
    source_manifest.symlink_to(real_manifest.name)

    with pytest.raises(campaign_error, match="symbolic-link campaign execution artifact"):
        create_campaign_merge(
            artifact,
            allow_partial=True,
            merge_id="merge-a",
        )



def test_campaign_merge_accepts_reconciled_observation_after_finalization_journal_crash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    run = frozen_campaign_plan(artifact).runs[0]
    logical_run_id = artifact.manifest["runs"][0]["run_id"]
    context = _execute_campaign_runs(artifact, package, (run,), "execution-a")

    # Simulate process loss after the valid Run Record and successful environment
    # finalization are durable, but before attempt_finalized/top-level completion.
    events_path = context.session_dir / "campaign_events.jsonl"
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    finalization_position = next(
        index for index, event in enumerate(events) if event.get("event") == "attempt_finalized"
    )
    events = events[:finalization_position]
    events_path.write_text(
        "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events),
        encoding="utf-8",
    )
    (context.session_dir / "experiment_execution.yaml").unlink()
    result_dir = next((context.session_dir / "runs").glob("run_*"))
    (result_dir / "environment_finalization.yaml").write_text(
        yaml.safe_dump({"ok": True, "result": {"restored": True}}, sort_keys=False),
        encoding="utf-8",
    )

    campaign_execution_path = context.session_dir / "campaign_execution.json"
    campaign_execution = json.loads(campaign_execution_path.read_text(encoding="utf-8"))
    campaign_execution["execution"]["status"] = "running"
    campaign_execution["execution"].pop("ended_at", None)
    campaign_execution["execution"].pop("experiment_execution_artifact", None)
    campaign_execution["outcomes"] = {
        "observed_run_ids": [],
        "pending_run_ids": [logical_run_id],
    }
    campaign_execution_path.write_text(
        json.dumps(campaign_execution, indent=2) + "\n",
        encoding="utf-8",
    )

    reconciled = reconcile_campaign_executions(artifact)
    assert reconciled == ("execution-a",)
    reconciled_manifest = json.loads(campaign_execution_path.read_text(encoding="utf-8"))
    assert reconciled_manifest["execution"]["status"] == "interrupted_unclean"
    assert reconciled_manifest["outcomes"]["observed_run_ids"] == [logical_run_id]

    merged = create_campaign_merge(
        artifact,
        allow_partial=True,
        merge_id="merge-a",
    )
    assert [item["logical_run_id"] for item in merged.manifest["observations"]] == [
        logical_run_id
    ]


def test_campaign_merge_rejects_tampered_diagnostic_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    run = frozen_campaign_plan(artifact).runs[0]
    context = _execute_campaign_runs(artifact, package, (run,), "execution-a")

    result_dir = next((context.session_dir / "runs").glob("run_*"))
    diagnostic_path = result_dir / "diagnostics" / "fixture.txt"
    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    original = b"diagnostic-original"
    diagnostic_path.write_bytes(original)

    record_path = result_dir / "run_record.yaml"
    record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    record["diagnostics"] = [
        {
            "id": "fixture_diagnostic",
            "artifact": "diagnostics/fixture.txt",
            "sha256": hashlib.sha256(original).hexdigest(),
        }
    ]
    record_path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    diagnostic_path.write_bytes(b"diagnostic-tampered")

    with pytest.raises(campaign_error, match="Diagnostic SHA-256 mismatch"):
        create_campaign_merge(
            artifact,
            allow_partial=True,
            merge_id="merge-a",
        )

def test_campaign_merge_v0_1_manifest_remains_valid(tmp_path: Path, monkeypatch) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    merged = create_campaign_merge(
        artifact,
        allow_partial=True,
        merge_id="merge-a",
    )
    legacy = dict(merged.manifest)
    legacy["schema_version"] = "0.1"
    legacy["population"] = dict(legacy["population"])
    legacy["population"]["blocked"] = 0

    validate_campaign_merge_manifest(legacy)



def test_campaign_merge_reports_coarse_progress(
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
    _execute_campaign_runs(artifact, package, runs, "execution-a")
    lines: list[str] = []

    create_campaign_merge(
        artifact,
        merge_id="merge-progress",
        progress=lines.append,
    )

    assert lines == [f"Merge: {len(runs)}/{len(runs)} observations assembled..."]
