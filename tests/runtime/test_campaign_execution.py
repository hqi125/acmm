from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from acmm.campaign.model import create_campaign
from acmm.campaign.execution import (
    create_campaign_execution_context,
    eligible_campaign_runs,
    finalize_campaign_execution,
    frozen_campaign_plan,
    initialize_campaign_execution,
    inspect_campaign_status,
    order_campaign_runs_for_execution,
    reconcile_campaign_executions,
    resumable_campaign_runs,
)
from acmm.runtime.planner import planned_run
from experiment_package.support import install_test_package


def test_campaign_execution_finalization_updates_durable_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    runs = frozen_campaign_plan(artifact).runs[:2]
    context = create_campaign_execution_context(
        artifact,
        runs,
        execution_id="execution-a",
    )
    context.session_dir.mkdir(parents=True)
    journal = initialize_campaign_execution(
        context,
        started_at=datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc),
    )
    journal.emit("subject_execution_started", {"run_index": runs[0].run_index, "attempt": 1})
    journal.emit("subject_execution_started", {"run_index": runs[1].run_index, "attempt": 1})

    result = SimpleNamespace(
        ended_at=datetime(2026, 11, 11, 12, 1, tzinfo=timezone.utc),
        manifest_path=context.session_dir / "experiment_execution.yaml",
        slots=(
            SimpleNamespace(
                planned=runs[0],
                is_resolved=True,
                attempts=(SimpleNamespace(),),
            ),
            SimpleNamespace(
                planned=runs[1],
                is_resolved=False,
                attempts=(SimpleNamespace(),),
            ),
        ),
    )
    (context.session_dir / "experiment_execution.yaml").write_text(
        "schema_version: '0.1'\n",
        encoding="utf-8",
    )

    finalized = finalize_campaign_execution(
        context,
        result,
        status="completed",
    )
    snapshot = inspect_campaign_status(artifact)

    assert finalized["execution"]["status"] == "completed"
    assert len(finalized["outcomes"]["observed_run_ids"]) == 1
    assert len(finalized["outcomes"]["pending_run_ids"]) == 1
    assert snapshot.runs.observed == 1
    assert snapshot.runs.pending == artifact.manifest["campaign"]["run_count"] - 1
    assert snapshot.executions.completed == 1


def test_campaign_execution_keeps_pre_exposure_failed_attempt_pending(
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
    context = create_campaign_execution_context(
        artifact,
        (run,),
        execution_id="execution-a",
    )
    context.session_dir.mkdir(parents=True)
    initialize_campaign_execution(context)
    result = SimpleNamespace(
        ended_at=datetime(2026, 11, 11, 12, 1, tzinfo=timezone.utc),
        manifest_path=context.session_dir / "experiment_execution.yaml",
        slots=(
            SimpleNamespace(
                planned=run,
                is_resolved=False,
                attempts=(SimpleNamespace(),),
            ),
        ),
    )
    (context.session_dir / "experiment_execution.yaml").write_text(
        "schema_version: '0.1'\n",
        encoding="utf-8",
    )

    finalized = finalize_campaign_execution(context, result, status="completed")

    assert len(finalized["outcomes"]["pending_run_ids"]) == 1



def test_reconciliation_keeps_started_uncaptured_attempt_resumable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    runs = frozen_campaign_plan(artifact).runs[:2]
    context = create_campaign_execution_context(
        artifact,
        runs,
        execution_id="execution-a",
    )
    context.session_dir.mkdir(parents=True)
    journal = initialize_campaign_execution(context)
    journal.emit("subject_execution_started", {"run_index": runs[0].run_index, "attempt": 1})

    reconciled = reconcile_campaign_executions(artifact)
    snapshot = inspect_campaign_status(artifact)

    assert reconciled == ("execution-a",)
    assert snapshot.runs.pending == artifact.manifest["campaign"]["run_count"]
    assert snapshot.executions.interrupted == 1

    eligible = eligible_campaign_runs(artifact, runs, snapshot)
    assert eligible == runs


def test_reconciliation_recognizes_safe_failure_manifest(
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
    context = create_campaign_execution_context(
        artifact,
        (run,),
        execution_id="execution-failed",
    )
    context.session_dir.mkdir(parents=True)
    journal = initialize_campaign_execution(context)
    journal.emit(
        "subject_execution_started",
        {"run_index": run.run_index, "attempt": 1},
    )
    journal.emit(
        "attempt_finalized",
        {"run_index": run.run_index, "attempt": 1},
    )

    run_dir = context.session_dir / "runs" / "run_000000_attempt-a"
    run_dir.mkdir(parents=True)
    (run_dir / "run_record.yaml").write_text(
        yaml.safe_dump(
            {
                "run": {"run_index": run.run_index},
                "execution": {"validity": "valid"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (context.session_dir / "experiment_failure.yaml").write_text(
        "schema_version: '0.1'\nexecution:\n  status: failed\n",
        encoding="utf-8",
    )

    reconciled = reconcile_campaign_executions(artifact)
    manifest = yaml.safe_load(
        (context.session_dir / "campaign_execution.json").read_text(
            encoding="utf-8"
        )
    )
    snapshot = inspect_campaign_status(artifact)

    assert reconciled == ("execution-failed",)
    assert manifest["execution"]["status"] == "failed"
    assert (
        manifest["execution"]["experiment_failure_artifact"]
        == "experiment_failure.yaml"
    )
    assert snapshot.runs.observed == 1
    assert snapshot.executions.failed == 1


def test_reconciliation_keeps_pre_exposure_invalid_record_pending(
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
    context = create_campaign_execution_context(
        artifact,
        (run,),
        execution_id="execution-a",
    )
    context.session_dir.mkdir(parents=True)
    journal = initialize_campaign_execution(context)
    journal.emit("attempt_finalized", {"run_index": run.run_index, "attempt": 1})

    run_dir = context.session_dir / "runs" / "run_000000_attempt-a"
    run_dir.mkdir(parents=True)
    (run_dir / "run_record.yaml").write_text(
        yaml.safe_dump(
            {
                "run": {"run_index": run.run_index},
                "execution": {"validity": "invalid"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    reconcile_campaign_executions(artifact)
    snapshot = inspect_campaign_status(artifact)

    assert snapshot.runs.observed == 0
    assert snapshot.runs.pending == artifact.manifest["campaign"]["run_count"]



def test_reconciliation_recovers_durably_persisted_valid_observation(
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
    context = create_campaign_execution_context(
        artifact,
        (run,),
        execution_id="execution-a",
    )
    context.session_dir.mkdir(parents=True)
    journal = initialize_campaign_execution(context)
    journal.emit("subject_execution_started", {"run_index": run.run_index, "attempt": 1})
    journal.emit("attempt_finalized", {"run_index": run.run_index, "attempt": 1})

    run_dir = context.session_dir / "runs" / "run_000000_attempt-a"
    run_dir.mkdir(parents=True)
    (run_dir / "run_record.yaml").write_text(
        yaml.safe_dump(
            {
                "run": {"run_index": run.run_index},
                "execution": {"validity": "valid"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    reconcile_campaign_executions(artifact)
    snapshot = inspect_campaign_status(artifact)

    assert snapshot.runs.observed == 1
    assert snapshot.executions.interrupted == 1


def test_graceful_interruption_keeps_unresolved_run_resumable(
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

    before_context = create_campaign_execution_context(
        artifact,
        (run,),
        execution_id="execution-before-subject",
    )
    before_context.session_dir.mkdir(parents=True)
    initialize_campaign_execution(before_context)
    before_result = SimpleNamespace(
        ended_at=datetime(2026, 11, 11, 12, 1, tzinfo=timezone.utc),
        interruption_path=before_context.session_dir / "experiment_interruption.yaml",
        slots=(
            SimpleNamespace(
                planned=run,
                is_resolved=False,
                attempts=(),
            ),
        ),
        interrupted_run_index=run.run_index,
        interrupted_attempt=1,
    )
    (before_context.session_dir / "experiment_interruption.yaml").write_text(
        "schema_version: '0.1'\n",
        encoding="utf-8",
    )
    before_manifest = finalize_campaign_execution(
        before_context,
        before_result,
        status="interrupted",
    )
    assert len(before_manifest["outcomes"]["pending_run_ids"]) == 1

    after_context = create_campaign_execution_context(
        artifact,
        (run,),
        execution_id="execution-after-subject",
    )
    after_context.session_dir.mkdir(parents=True)
    journal = initialize_campaign_execution(after_context)
    journal.emit(
        "subject_execution_started",
        {"run_index": run.run_index, "attempt": 1},
    )
    after_result = SimpleNamespace(
        ended_at=datetime(2026, 11, 11, 12, 2, tzinfo=timezone.utc),
        interruption_path=after_context.session_dir / "experiment_interruption.yaml",
        slots=(
            SimpleNamespace(
                planned=run,
                is_resolved=False,
                attempts=(),
            ),
        ),
        interrupted_run_index=run.run_index,
        interrupted_attempt=1,
    )
    (after_context.session_dir / "experiment_interruption.yaml").write_text(
        "schema_version: '0.1'\n",
        encoding="utf-8",
    )
    after_manifest = finalize_campaign_execution(
        after_context,
        after_result,
        status="interrupted",
    )
    assert len(after_manifest["outcomes"]["pending_run_ids"]) == 1


def test_campaign_execution_lock_rejects_concurrent_local_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )

    from acmm.campaign.execution import campaign_execution_lock
    from acmm.campaign.model import campaign_error

    with campaign_execution_lock(artifact):
        with pytest.raises(campaign_error, match="already active"):
            with campaign_execution_lock(artifact):
                pass

    assert not (artifact.path / ".campaign-execute.lock").exists()


def test_campaign_execution_lock_reclaims_stale_local_process_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )

    import json
    import socket

    from acmm.campaign.execution import campaign_execution_lock

    lock_path = artifact.path / ".campaign-execute.lock"
    lock_path.write_text(
        json.dumps(
            {
                "token": "stale",
                "pid": 2147483647,
                "host": socket.gethostname(),
                "acquired_at": "2026-11-11T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    with campaign_execution_lock(artifact):
        assert lock_path.exists()

    assert not lock_path.exists()


def test_campaign_event_reader_uses_last_complete_durable_event(
    tmp_path: Path,
) -> None:
    from acmm.campaign.execution import read_campaign_events

    path = tmp_path / "campaign_events.jsonl"
    path.write_bytes(
        b'{"sequence":1,"event":"first"}\n'
        b'{"sequence":2,"event":"second"}\n'
        b'{"sequence":3,"event":"torn"'
    )

    log = read_campaign_events(path)

    assert [event["event"] for event in log.events] == ["first", "second"]
    assert log.truncated_tail is True


def test_campaign_event_reader_rejects_corruption_before_tail(
    tmp_path: Path,
) -> None:
    from acmm.campaign.execution import read_campaign_events
    from acmm.campaign.model import campaign_error

    path = tmp_path / "campaign_events.jsonl"
    path.write_bytes(
        b'{"sequence":1,"event":"first"}\n'
        b'not-json\n'
        b'{"sequence":3,"event":"third"}\n'
    )

    with pytest.raises(campaign_error, match="corrupted before its durable tail"):
        read_campaign_events(path)

def test_campaign_event_reader_ignores_complete_json_without_durable_newline(
    tmp_path: Path,
) -> None:
    from acmm.campaign.execution import read_campaign_events

    path = tmp_path / "campaign_events.jsonl"
    path.write_bytes(
        b'{"sequence":1,"event":"first"}\n'
        b'{"sequence":2,"event":"not-durable"}'
    )

    log = read_campaign_events(path)

    assert [event["event"] for event in log.events] == ["first"]
    assert log.truncated_tail is True



def test_campaign_execution_records_resume_source(tmp_path: Path, monkeypatch) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    run = frozen_campaign_plan(artifact).runs[0]
    context = create_campaign_execution_context(
        artifact,
        (run,),
        execution_id="execution-b",
        resumed_from_execution_id="execution-a",
    )
    context.session_dir.mkdir(parents=True)

    initialize_campaign_execution(context)

    manifest = json.loads(
        (context.session_dir / "campaign_execution.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == "0.2"
    assert manifest["execution"]["resumed_from_execution_id"] == "execution-a"
    assert manifest["outcomes"]["pending_run_ids"] == [
        artifact.manifest["runs"][0]["run_id"]
    ]


def test_resumable_campaign_runs_selects_latest_incomplete_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    runs = frozen_campaign_plan(artifact).runs[:3]

    first = create_campaign_execution_context(
        artifact,
        runs[:2],
        execution_id="execution-a",
    )
    first.session_dir.mkdir(parents=True)
    initialize_campaign_execution(
        first,
        started_at=datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc),
    )
    finalize_campaign_execution(
        first,
        SimpleNamespace(
            ended_at=datetime(2026, 11, 19, 12, 1, tzinfo=timezone.utc),
            slots=(
                SimpleNamespace(planned=runs[0], is_resolved=True, attempts=()),
                SimpleNamespace(planned=runs[1], is_resolved=False, attempts=()),
            ),
        ),
        status="completed",
    )

    second = create_campaign_execution_context(
        artifact,
        (runs[2],),
        execution_id="execution-b",
    )
    second.session_dir.mkdir(parents=True)
    initialize_campaign_execution(
        second,
        started_at=datetime(2026, 11, 19, 12, 2, tzinfo=timezone.utc),
    )
    finalize_campaign_execution(
        second,
        SimpleNamespace(
            ended_at=datetime(2026, 11, 19, 12, 3, tzinfo=timezone.utc),
            slots=(
                SimpleNamespace(planned=runs[2], is_resolved=False, attempts=()),
            ),
        ),
        status="completed",
    )

    status = inspect_campaign_status(artifact)
    source, selected = resumable_campaign_runs(artifact, status)
    assert source == "execution-b"
    assert selected == (runs[2],)

    source, selected = resumable_campaign_runs(
        artifact,
        status,
        source_execution_id="execution-a",
    )
    assert source == "execution-a"
    assert selected == (runs[1],)


def test_campaign_v0_1_blocked_slot_is_treated_as_resumable_pending(
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
    context = create_campaign_execution_context(
        artifact,
        (run,),
        execution_id="execution-a",
    )
    context.session_dir.mkdir(parents=True)
    initialize_campaign_execution(context)
    path = context.session_dir / "campaign_execution.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    logical_id = manifest["execution"]["selected_run_ids"][0]
    manifest["schema_version"] = "0.1"
    manifest["execution"]["status"] = "interrupted"
    manifest["execution"]["ended_at"] = "2026-11-19T12:00:00Z"
    manifest["outcomes"] = {
        "observed_run_ids": [],
        "blocked_run_ids": [logical_id],
        "pending_selected_run_ids": [],
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    snapshot = inspect_campaign_status(artifact)
    assert snapshot.runs.pending == artifact.manifest["campaign"]["run_count"]
    source, selected = resumable_campaign_runs(
        artifact,
        snapshot,
        source_execution_id="execution-a",
    )
    assert source == "execution-a"
    assert selected == (run,)


def test_multi_scenario_order_is_materialization_then_subject_major() -> None:
    artifact = SimpleNamespace(
        manifest={
            "schema_version": "0.2",
            "design": {
                "subjects": ["s1", "s2"],
                "materialization": {"group_by": ["scenario_id"]},
            },
        }
    )
    runs = (
        planned_run(0, "s2", "b", 1),
        planned_run(1, "s1", "a", 1),
        planned_run(2, "s1", "b", 1),
        planned_run(3, "s2", "a", 1),
    )

    ordered = order_campaign_runs_for_execution(artifact, runs)

    assert [(run.scenario_id, run.subject_id) for run in ordered] == [
        ("b", "s1"),
        ("b", "s2"),
        ("a", "s1"),
        ("a", "s2"),
    ]


def test_interrupted_campaign_selection_resumes_in_new_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from acmm.campaign.recovery import reconcile_campaign
    from acmm.runtime.experiment_execution import (
        execute_experiment,
        experiment_execution_interrupted,
    )
    from acmm.runtime.interfaces import (
        adapter_execution_result,
        captured_evidence,
        evaluation_context,
        judgment_result,
    )
    from acmm.runtime.registry import execution_registry

    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    selected = frozen_campaign_plan(artifact).runs[:2]

    class _interrupt_adapter:
        def execute(self, subject, scenario, environment, termination):
            del subject, scenario, environment, termination
            raise KeyboardInterrupt

    class _success_adapter:
        def execute(self, subject, scenario, environment, termination):
            del scenario, environment, termination
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
                detail="resume integration fixture",
            )

    def registry(adapter):
        return execution_registry(
            subject_adapters={
                subject["id"]: adapter for subject in package.contract["subjects"]
            },
            evaluators={"always_compliant": _evaluator()},
        )

    source = create_campaign_execution_context(
        artifact,
        selected,
        execution_id="execution-source",
    )
    source_journal = [None]

    def source_ready(session_dir: Path) -> None:
        assert session_dir == source.session_dir
        source_journal[0] = initialize_campaign_execution(source)

    def source_event(event: str, payload) -> None:
        assert source_journal[0] is not None
        source_journal[0].emit(event, payload)

    with pytest.raises(experiment_execution_interrupted) as interrupted:
        execute_experiment(
            contract=dict(package.contract),
            contract_path=artifact.path / "contract.yaml",
            registry=registry(_interrupt_adapter()),
            results_root=artifact.path,
            harness_version="test-harness",
            run_indices=tuple(run.run_index for run in selected),
            session_parent=source.session_parent,
            execution_id_factory=lambda: source.execution_id,
            session_ready=source_ready,
            event_sink=source_event,
            sleep=lambda seconds: None,
            package_provenance={
                "schema_version": package.schema_version,
                "sha256": package.package_sha256,
                "contract_sha256": package.contract_sha256,
                "runtime_sha256": (
                    package.runtime.sha256 if package.runtime is not None else None
                ),
            },
        )

    finalize_campaign_execution(
        source,
        interrupted.value.result,
        status="interrupted",
    )
    reconcile_campaign(artifact)
    status = inspect_campaign_status(artifact)
    source_execution_id, resumable = resumable_campaign_runs(
        artifact,
        status,
        source_execution_id="execution-source",
    )
    assert source_execution_id == "execution-source"
    assert resumable == selected

    resumed = create_campaign_execution_context(
        artifact,
        resumable,
        execution_id="execution-resumed",
        resumed_from_execution_id=source_execution_id,
    )
    resumed_journal = [None]

    def resumed_ready(session_dir: Path) -> None:
        assert session_dir == resumed.session_dir
        resumed_journal[0] = initialize_campaign_execution(resumed)

    def resumed_event(event: str, payload) -> None:
        assert resumed_journal[0] is not None
        resumed_journal[0].emit(event, payload)

    result = execute_experiment(
        contract=dict(package.contract),
        contract_path=artifact.path / "contract.yaml",
        registry=registry(_success_adapter()),
        results_root=artifact.path,
        harness_version="test-harness",
        run_indices=tuple(run.run_index for run in resumable),
        session_parent=resumed.session_parent,
        execution_id_factory=lambda: resumed.execution_id,
        session_ready=resumed_ready,
        event_sink=resumed_event,
        sleep=lambda seconds: None,
        package_provenance={
            "schema_version": package.schema_version,
            "sha256": package.package_sha256,
            "contract_sha256": package.contract_sha256,
            "runtime_sha256": (
                package.runtime.sha256 if package.runtime is not None else None
            ),
        },
    )
    finalize_campaign_execution(resumed, result, status="completed")

    final_status = inspect_campaign_status(artifact)
    assert final_status.runs.observed == len(selected)
    assert final_status.runs.pending == (
        artifact.manifest["campaign"]["run_count"] - len(selected)
    )
    resumed_manifest = json.loads(
        (resumed.session_dir / "campaign_execution.json").read_text(encoding="utf-8")
    )
    assert resumed_manifest["execution"]["resumed_from_execution_id"] == (
        "execution-source"
    )
