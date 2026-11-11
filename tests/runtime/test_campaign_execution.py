from __future__ import annotations

from datetime import datetime, timezone
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
    reconcile_campaign_executions,
)
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
    assert len(finalized["outcomes"]["blocked_run_ids"]) == 1
    assert snapshot.runs.observed == 1
    assert snapshot.runs.blocked == 1
    assert snapshot.runs.pending == artifact.manifest["campaign"]["run_count"] - 2
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

    assert finalized["outcomes"]["blocked_run_ids"] == []
    assert len(finalized["outcomes"]["pending_selected_run_ids"]) == 1



def test_reconciliation_blocks_started_uncaptured_attempt_and_keeps_unstarted_pending(
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
    assert snapshot.runs.blocked == 1
    assert snapshot.runs.pending == artifact.manifest["campaign"]["run_count"] - 1
    assert snapshot.executions.interrupted == 1

    eligible = eligible_campaign_runs(artifact, runs, snapshot)
    assert eligible == (runs[1],)


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
    assert snapshot.runs.blocked == 0
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
    assert snapshot.runs.blocked == 0
    assert snapshot.executions.interrupted == 1


def test_graceful_interruption_blocks_only_after_subject_execution_started(
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
    assert before_manifest["outcomes"]["blocked_run_ids"] == []
    assert len(before_manifest["outcomes"]["pending_selected_run_ids"]) == 1

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
    assert len(after_manifest["outcomes"]["blocked_run_ids"]) == 1
    assert after_manifest["outcomes"]["pending_selected_run_ids"] == []


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

