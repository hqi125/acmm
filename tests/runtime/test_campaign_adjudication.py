from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from acmm.campaign.adjudication import (
    create_campaign_adjudication,
    iter_campaign_adjudication_runs,
    load_campaign_adjudication,
)
from acmm.campaign.execution import (
    create_campaign_execution_context,
    finalize_campaign_execution,
    frozen_campaign_plan,
    initialize_campaign_execution,
)
from acmm.campaign.merge import create_campaign_merge, iter_campaign_merge_observations
from acmm.campaign.model import create_campaign
from acmm.record.loader import load_run_record
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


def _execute_all(artifact, package) -> None:
    runs = frozen_campaign_plan(artifact).runs
    context = create_campaign_execution_context(
        artifact,
        runs,
        execution_id="execution-a",
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


REVISED_RUNTIME = '''from acmm.adjudication_api import experiment_adjudicator
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


def test_campaign_adjudication_is_immutable_and_merge_can_apply_revision(
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
    _execute_all(artifact, acquisition)

    source_records = sorted(artifact.path.glob("executions/*/runs/*/run_record.yaml"))
    assert source_records
    original_hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_records
    }
    assert all(load_run_record(path)["judgments"][0]["outcome"] == "compliant" for path in source_records)

    revised, _ = install_test_package(
        tmp_path,
        monkeypatch,
        runtime_source=REVISED_RUNTIME,
        source_name="revised-source",
        with_adjudication=True,
    )
    adjudication = create_campaign_adjudication(
        artifact,
        revised,
        adjudication_id="revision-a",
    )

    assert adjudication.manifest["population"] == {
        "total": 6,
        "adjudicated": 6,
        "changed_runs": 6,
        "changed_judgments": 6,
    }
    assert adjudication.manifest["adjudication"]["acquisition_package_sha256"] == acquisition.package_sha256
    assert adjudication.manifest["adjudication"]["adjudicator_package_sha256"] == revised.package_sha256
    for path, before in original_hashes.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        assert load_run_record(path)["judgments"][0]["outcome"] == "compliant"

    merged = create_campaign_merge(
        artifact,
        merge_id="merge-a",
        adjudication_id="revision-a",
    )
    assert merged.manifest["adjudication"]["id"] == "revision-a"
    for observation in iter_campaign_merge_observations(merged.path, merged.manifest):
        record = load_run_record(merged.path / observation["merged_result"] / "run_record.yaml")
        assert record["judgments"][0]["outcome"] == "violation"

    for path, before in original_hashes.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_campaign_adjudication_requires_identical_contract(
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
    _execute_all(artifact, acquisition)

    revised, _ = install_test_package(
        tmp_path,
        monkeypatch,
        runtime_source=REVISED_RUNTIME,
        source_name="revised-source",
        with_adjudication=True,
    )
    contract_path = revised.root / "experiment.yaml"
    value = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    value["experiment"]["title"] += " changed"
    # Mutating a staged package is rejected even before Contract equivalence is considered.
    contract_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    from acmm.campaign.model import campaign_error
    import pytest

    with pytest.raises((campaign_error, ValueError)):
        create_campaign_adjudication(artifact, revised, adjudication_id="revision-b")


def test_campaign_adjudication_reuses_frozen_runtime_evaluation_path(
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
    _execute_all(artifact, acquisition)

    revised, _ = install_test_package(
        tmp_path,
        monkeypatch,
        runtime_source=REVISED_RUNTIME,
        source_name="revised-source",
        with_adjudication=True,
    )

    from acmm.runtime import execution as runtime_execution

    original = runtime_execution._evaluate_opportunities
    calls: list[int] = []

    def traced(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_execution, "_evaluate_opportunities", traced)

    adjudication = create_campaign_adjudication(
        artifact,
        revised,
        adjudication_id="revision-shared-path",
    )

    assert adjudication.manifest["population"]["adjudicated"] == 6
    assert len(calls) == 6


def test_campaign_adjudication_snapshots_exact_adjudicator_package(
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
    _execute_all(artifact, acquisition)
    revised, _ = install_test_package(
        tmp_path,
        monkeypatch,
        runtime_source=REVISED_RUNTIME,
        source_name="revised-source",
        with_adjudication=True,
    )

    adjudication = create_campaign_adjudication(
        artifact,
        revised,
        adjudication_id="revision-snapshot",
    )

    identity = adjudication.manifest["adjudication"]
    assert adjudication.manifest["schema_version"] == "0.3"
    assert identity["adjudicator_snapshot_artifact"] == "package_store"
    snapshot_root = (
        adjudication.path
        / "package_store"
        / "_experiment_packages"
        / revised.experiment_id
        / revised.package_sha256
    )
    assert snapshot_root.is_dir()
    assert (snapshot_root / "experiment-package.yaml").is_file()

    loaded = load_campaign_adjudication(artifact, adjudication.adjudication_id)
    assert loaded.manifest["adjudication"]["adjudicator_package_sha256"] == revised.package_sha256


def test_campaign_adjudication_reports_coarse_progress(
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
    _execute_all(artifact, acquisition)
    revised, _ = install_test_package(
        tmp_path,
        monkeypatch,
        runtime_source=REVISED_RUNTIME,
        source_name="revised-source",
        with_adjudication=True,
    )
    lines: list[str] = []

    create_campaign_adjudication(
        artifact,
        revised,
        adjudication_id="revision-progress",
        progress=lines.append,
    )

    assert lines[0] == "Adjudication: inspecting Campaign completeness and frozen provenance..."
    assert "Adjudication: snapshotting adjudicator package..." in lines
    assert "Adjudication: 6/6 observations processed..." in lines
    assert lines[-1] == "Adjudication: validating and publishing bounded adjudication artifacts..."


def test_campaign_adjudication_v0_1_manifest_remains_valid(
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
    _execute_all(artifact, acquisition)
    revised, _ = install_test_package(
        tmp_path,
        monkeypatch,
        runtime_source=REVISED_RUNTIME,
        source_name="revised-source",
        with_adjudication=True,
    )
    adjudication = create_campaign_adjudication(
        artifact,
        revised,
        adjudication_id="revision-legacy-schema",
    )
    legacy = dict(adjudication.manifest)
    legacy["schema_version"] = "0.1"
    legacy["adjudication"] = dict(legacy["adjudication"])
    legacy["adjudication"].pop("adjudicator_snapshot_artifact")
    legacy["runs"] = list(iter_campaign_adjudication_runs(adjudication))
    legacy.pop("runs_artifact", None)

    from acmm.campaign.adjudication import validate_campaign_adjudication_manifest

    validate_campaign_adjudication_manifest(legacy)
