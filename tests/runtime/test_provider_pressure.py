from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from acmm.runtime.provider_pressure import (
    provider_pressure_engine,
    runtime_provider_pressure_action_result_event,
    runtime_provider_pressure_app_suspension,
    runtime_provider_pressure_app_policy,
    runtime_provider_pressure_execution_context,
    runtime_provider_pressure_identity,
    runtime_provider_pressure_observation,
    runtime_provider_pressure_pacing_event,
    runtime_provider_pressure_policy,
    runtime_provider_pressure_recovery_event,
    runtime_provider_pressure_run_context,
    runtime_provider_pressure_run_outcome,
    runtime_provider_pressure_signal,
    runtime_provider_pressure_workload,
)


class fake_pressure_model:
    def __init__(self) -> None:
        self.measurements: list[int] = []

    def identity(self, context):
        return runtime_provider_pressure_identity(
            provider_id="example",
            scope_id="https://api.example.test|account",
            model_id="example_pressure_v1",
            model_version="0.4",
            model_definition_sha256="a" * 64,
        )

    def policy(self, context):
        return runtime_provider_pressure_policy(
            punishment_increment=1.5,
            reward_decrement=0.1,
            base_delay_seconds=10.0,
            max_delay_seconds=120.0,
        )

    def historical_replay(self, context):
        return (
            runtime_provider_pressure_observation(
                source_kind="historical_replay",
                source_execution_id="prior",
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
            features={"operation.create": 1.0},
            metadata={"kind": "create"},
        )

    def run_measurement_start(self, context):
        self.measurements.append(context.run_index)
        return {"run_index": context.run_index}

    def pacing_observe(self, context, event):
        return runtime_provider_pressure_observation(
            source_kind="live_execution",
            source_execution_id=context.execution_id,
            signals=(),
            metadata={"reason": event.reason},
        )

    def run_observe(self, context, measurement_handle, outcome):
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
            metadata={"resolved": outcome.resolved},
        )


def _execution_context(tmp_path: Path, execution_id: str):
    session = tmp_path / "results" / "example" / f"execution_{execution_id}"
    session.mkdir(parents=True)
    return runtime_provider_pressure_execution_context(
        experiment_id="example",
        execution_id=execution_id,
        selected_run_indices=(0,),
        session_dir=str(session),
        generated_root=str(tmp_path),
        package_sha256="b" * 64,
    )


def _run_context(tmp_path: Path, execution_id: str):
    return runtime_provider_pressure_run_context(
        experiment_id="example",
        execution_id=execution_id,
        run_index=0,
        subject_id="subject",
        scenario_id="scenario",
        repetition=1,
        materialization_key="run_index=0",
        session_dir=str(tmp_path / "results" / "example" / f"execution_{execution_id}"),
    )


def test_pph_replays_history_acts_on_action_and_exports_hash_chained_trajectory(tmp_path: Path) -> None:
    model = fake_pressure_model()
    waits: list[float] = []
    engine = provider_pressure_engine(
        model,
        sleep=waits.append,
        now=lambda: datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc),
    )
    session = engine.execution_initialize(_execution_context(tmp_path, "live"))

    feature = session.profile["features"]["operation.create"]
    assert feature["score"] == 2.5
    assert feature["punishments"] == 1
    assert session.applied_historical_sources == ["prior"]

    run_admission = engine.run_admit(session, _run_context(tmp_path, "live"))
    assert waits == []
    assert run_admission.record["recommended_wait_seconds"] == 0.0
    assert model.measurements == [0]

    action = engine.action_admit(
        session,
        _run_context(tmp_path, "live"),
        action_id="operation.create",
        workload=runtime_provider_pressure_workload(
            features={"operation.create": 1.0}
        ),
    )
    assert waits == [15.0]
    assert action.record["effective_pressure"] == 2.5
    assert action.record["recommended_wait_seconds"] == 15.0

    engine.run_finalize(
        session,
        run_admission,
        runtime_provider_pressure_run_outcome(
            completed=True,
            resolved=True,
            attempt_count=1,
            terminations=("agent_completed",),
        ),
    )
    assert session.profile["features"]["operation.create"]["score"] == 2.4

    snapshot = engine.execution_finalize(session, status="completed")
    trajectory = sorted(snapshot.parent.glob("*.yaml"))
    assert len(trajectory) == 3
    bootstrap = yaml.safe_load(trajectory[0].read_text(encoding="utf-8"))
    replay = yaml.safe_load(trajectory[1].read_text(encoding="utf-8"))
    live = yaml.safe_load(trajectory[2].read_text(encoding="utf-8"))
    assert replay["snapshot"]["parent_sha256"] == bootstrap["snapshot"]["snapshot_sha256"]
    assert live["snapshot"]["parent_sha256"] == replay["snapshot"]["snapshot_sha256"]
    assert live["learning_delta"]["signals"][0]["kind"] == "reward"
    assert live["learning_delta"]["decisions"][1]["action_id"] == "operation.create"
    assert live["learning_delta"]["pacing_events"][0]["source"] == "pph"


def test_pph_does_not_replay_same_historical_execution_twice(tmp_path: Path) -> None:
    model = fake_pressure_model()
    engine = provider_pressure_engine(
        model,
        sleep=lambda _seconds: None,
        now=lambda: datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc),
    )
    first = engine.execution_initialize(_execution_context(tmp_path, "first"))
    engine.execution_finalize(first, status="completed")
    second = engine.execution_initialize(_execution_context(tmp_path, "second"))
    assert second.applied_historical_sources == []
    assert second.profile["features"]["operation.create"]["punishments"] == 1


def test_pph_does_not_reimport_a_prior_live_execution_as_history(tmp_path: Path) -> None:
    class replay_live_model(fake_pressure_model):
        def historical_replay(self, context):
            if context.execution_id == "first":
                return ()
            return (
                runtime_provider_pressure_observation(
                    source_kind="historical_replay",
                    source_execution_id="first",
                    signals=(
                        runtime_provider_pressure_signal(
                            kind="punishment",
                            count=1,
                            features={"operation.create": 1.0},
                        ),
                    ),
                ),
            )

    model = replay_live_model()
    engine = provider_pressure_engine(
        model,
        sleep=lambda _seconds: None,
        now=lambda: datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc),
    )
    first = engine.execution_initialize(_execution_context(tmp_path, "first"))
    admission = engine.run_admit(first, _run_context(tmp_path, "first"))
    engine.run_finalize(
        first,
        admission,
        runtime_provider_pressure_run_outcome(
            completed=True,
            resolved=True,
            attempt_count=1,
            terminations=("agent_completed",),
        ),
    )
    engine.execution_finalize(first, status="completed")
    second = engine.execution_initialize(_execution_context(tmp_path, "second"))
    assert second.applied_historical_sources == []
    assert second.profile["features"]["operation.create"]["punishments"] == 0


def test_pph_unrelated_features_do_not_inherit_pressure(tmp_path: Path) -> None:
    waits: list[float] = []
    engine = provider_pressure_engine(
        fake_pressure_model(),
        sleep=waits.append,
        now=lambda: datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc),
    )
    session = engine.execution_initialize(_execution_context(tmp_path, "unrelated"))
    action = engine.action_admit(
        session,
        _run_context(tmp_path, "unrelated"),
        action_id="operation.read",
        workload=runtime_provider_pressure_workload(
            features={"operation.read": 1.0}
        ),
    )
    assert action.record["effective_pressure"] == 1.0
    assert action.record["recommended_wait_seconds"] == 0.0
    assert waits == []


def test_pph_shared_declared_feature_transfers_pressure(tmp_path: Path) -> None:
    class shared_feature_model(fake_pressure_model):
        def historical_replay(self, context):
            return (
                runtime_provider_pressure_observation(
                    source_kind="historical_replay",
                    source_execution_id="prior",
                    signals=(
                        runtime_provider_pressure_signal(
                            kind="punishment",
                            count=1,
                            features={
                                "semantic.create": 0.5,
                                "operation.repository.create": 1.0,
                            },
                        ),
                    ),
                ),
            )

    waits: list[float] = []
    engine = provider_pressure_engine(
        shared_feature_model(),
        sleep=waits.append,
        now=lambda: datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc),
    )
    session = engine.execution_initialize(_execution_context(tmp_path, "shared"))
    action = engine.action_admit(
        session,
        _run_context(tmp_path, "shared"),
        action_id="issue.create",
        workload=runtime_provider_pressure_workload(
            features={
                "semantic.create": 0.5,
                "operation.issue.create": 1.0,
            }
        ),
    )
    assert action.record["effective_pressure"] > 1.0
    assert action.record["recommended_wait_seconds"] > 0.0
    assert waits


def test_pph_runtime_pacing_punishes_action_immediately(tmp_path: Path) -> None:
    class event_model(fake_pressure_model):
        def historical_replay(self, context):
            return ()

        def pacing_observe(self, context, event):
            if event.reason != "provider_secondary_pressure":
                return runtime_provider_pressure_observation(
                    source_kind="live_execution",
                    source_execution_id=context.execution_id,
                )
            return runtime_provider_pressure_observation(
                source_kind="live_execution",
                source_execution_id=context.execution_id,
                signals=(
                    runtime_provider_pressure_signal(
                        kind="punishment",
                        count=1,
                        severity=1.0,
                        pressure_duration_seconds=event.duration_seconds,
                        features=event.features,
                    ),
                ),
            )

    waits: list[float] = []
    engine = provider_pressure_engine(
        event_model(),
        sleep=waits.append,
        now=lambda: datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc),
    )
    session = engine.execution_initialize(_execution_context(tmp_path, "event"))
    context = _run_context(tmp_path, "event")
    engine.pacing_observe(
        session,
        context,
        runtime_provider_pressure_pacing_event(
            source="runtime",
            reason="provider_secondary_pressure",
            duration_seconds=60.0,
            action_id="repository.create",
            features={"operation.repository.create": 1.0},
        ),
    )
    feature = session.profile["features"]["operation.repository.create"]
    assert feature["punishments"] == 1
    assert feature["runtime_pressure_events"] == 1
    assert feature["runtime_pressure_seconds"] == 60.0

    action = engine.action_admit(
        session,
        context,
        action_id="repository.create",
        workload=runtime_provider_pressure_workload(
            features={"operation.repository.create": 1.0}
        ),
    )
    assert action.record["recommended_wait_seconds"] == 15.0
    assert waits == [15.0]
    assert session.profile["totals"]["pph_pacing_events"] == 1
    assert session.profile["totals"]["pph_pacing_seconds"] == 15.0


def test_pph_runtime_pacing_does_not_leak_to_unrelated_action(tmp_path: Path) -> None:
    class event_model(fake_pressure_model):
        def historical_replay(self, context):
            return ()

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
                ),
            )

    waits: list[float] = []
    engine = provider_pressure_engine(
        event_model(),
        sleep=waits.append,
        now=lambda: datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc),
    )
    session = engine.execution_initialize(_execution_context(tmp_path, "event-read"))
    context = _run_context(tmp_path, "event-read")
    engine.pacing_observe(
        session,
        context,
        runtime_provider_pressure_pacing_event(
            source="runtime",
            reason="provider_secondary_pressure",
            duration_seconds=120.0,
            action_id="repository.create",
            features={"operation.repository.create": 1.0},
        ),
    )
    action = engine.action_admit(
        session,
        context,
        action_id="repository.read",
        workload=runtime_provider_pressure_workload(
            features={"operation.repository.read": 1.0}
        ),
    )
    assert action.record["recommended_wait_seconds"] == 0.0
    assert waits == []


def test_pph_provider_recovery_correlates_action_and_learns_only_underprediction(
    tmp_path: Path,
) -> None:
    class recovery_model(fake_pressure_model):
        def historical_replay(self, context):
            return (
                runtime_provider_pressure_observation(
                    source_kind="historical_replay",
                    source_execution_id="prior",
                    signals=(
                        runtime_provider_pressure_signal(
                            kind="punishment",
                            count=1,
                            features={"operation.create": 1.0},
                        ),
                    ),
                ),
            )

        def recovery_observe(self, context, event):
            if event.underprediction_seconds <= 0:
                return runtime_provider_pressure_observation(
                    source_kind="live_execution",
                    source_execution_id=context.execution_id,
                    signals=(),
                    metadata={"underprediction_seconds": 0.0},
                )
            return runtime_provider_pressure_observation(
                source_kind="live_execution",
                source_execution_id=context.execution_id,
                signals=(
                    runtime_provider_pressure_signal(
                        kind="punishment",
                        count=1,
                        severity=event.underprediction_seconds / 100.0,
                        pressure_duration_seconds=event.underprediction_seconds,
                        features=event.features,
                        metadata={
                            "decision_id": event.decision_id,
                            "provider_wait_seconds": event.wait_seconds,
                            "admission_wait_seconds": event.admission_wait_seconds,
                            "underprediction_seconds": event.underprediction_seconds,
                        },
                    ),
                ),
            )

    waits: list[float] = []
    engine = provider_pressure_engine(
        recovery_model(),
        sleep=waits.append,
        now=lambda: datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc),
    )
    session = engine.execution_initialize(_execution_context(tmp_path, "recovery"))
    context = _run_context(tmp_path, "recovery")
    action = engine.action_admit(
        session,
        context,
        action_id="operation.create",
        workload=runtime_provider_pressure_workload(
            features={"operation.create": 1.0}
        ),
    )

    recovery = engine.recovery_actuate(
        session,
        context,
        runtime_provider_pressure_recovery_event(
            kind="secondary",
            wait_seconds=60.0,
            recommendation_source="documented_fallback",
            action_id="operation.create",
            decision_id=str(action.record["decision_id"]),
            features={"operation.create": 1.0},
            metadata={"attempt": 1},
        ),
    )

    assert waits == [15.0, 60.0]
    assert recovery.record["decision_id"] == action.record["decision_id"]
    assert recovery.record["admission_wait_seconds"] == 15.0
    assert recovery.record["provider_wait_seconds"] == 60.0
    assert recovery.record["underprediction_seconds"] == 45.0
    assert recovery.record["signal_count"] == 1
    assert session.execution_signals[-1]["pressure_duration_seconds"] == 45.0
    assert session.pacing_events[-1]["reason"] == "provider_directed_recovery"
    assert session.pacing_events[-1]["duration_seconds"] == 60.0


def test_pph_provider_recovery_does_not_punish_when_wait_does_not_exceed_admission(
    tmp_path: Path,
) -> None:
    class recovery_model(fake_pressure_model):
        def historical_replay(self, context):
            return (
                runtime_provider_pressure_observation(
                    source_kind="historical_replay",
                    source_execution_id="prior",
                    signals=(
                        runtime_provider_pressure_signal(
                            kind="punishment",
                            count=2,
                            severity=1.0,
                            features={"operation.create": 1.0},
                        ),
                    ),
                ),
            )

        def recovery_observe(self, context, event):
            return runtime_provider_pressure_observation(
                source_kind="live_execution",
                source_execution_id=context.execution_id,
                signals=(
                    runtime_provider_pressure_signal(
                        kind="punishment",
                        count=1,
                        features=event.features,
                    ),
                ) if event.underprediction_seconds > 0 else (),
            )

    waits: list[float] = []
    engine = provider_pressure_engine(
        recovery_model(),
        sleep=waits.append,
        now=lambda: datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc),
    )
    session = engine.execution_initialize(_execution_context(tmp_path, "covered"))
    context = _run_context(tmp_path, "covered")
    action = engine.action_admit(
        session,
        context,
        action_id="operation.create",
        workload=runtime_provider_pressure_workload(
            features={"operation.create": 1.0}
        ),
    )
    assert action.record["recommended_wait_seconds"] == 30.0
    punishments_before = session.profile["features"]["operation.create"]["punishments"]

    recovery = engine.recovery_actuate(
        session,
        context,
        runtime_provider_pressure_recovery_event(
            kind="secondary",
            wait_seconds=20.0,
            recommendation_source="retry_after",
            action_id="operation.create",
            decision_id=str(action.record["decision_id"]),
            features={"operation.create": 1.0},
        ),
    )

    assert recovery.record["underprediction_seconds"] == 0.0
    assert recovery.record["signal_count"] == 0
    assert session.profile["features"]["operation.create"]["punishments"] == punishments_before
    assert waits == [30.0, 20.0]


def test_app_suspension_is_independent_from_event_pph_without_episode_state(
    tmp_path: Path,
) -> None:
    class adaptive_model(fake_pressure_model):
        def app_policy(self, context):
            return runtime_provider_pressure_app_policy(
                enabled=True,
                actions=("operation.create",),
            )

        def app_admission_suspension(self, context, action_id, workload, model_state):
            assert action_id == "operation.create"
            assert model_state == {}
            return runtime_provider_pressure_app_suspension(
                duration_seconds=30.0,
                metadata={
                    "eligible": True,
                    "matched_pocket_id": "pocket-0000",
                    "usage_vector": {"requests": 12.0},
                },
            )

        def app_probe_recovery_metadata(self, context, event, probe, model_state):
            assert probe["probe_id"].startswith("adaptive:run=0:app=")
            assert "__core_app" in model_state
            return {"usage_vector": {"requests": 12.0}}

        def app_probe_close(self, context, event, probe, model_state):
            model_state["adaptive_pressure_pocket"] = {"closed_probe_count": 1}
            return {"provider_geometry_updated": True}

    waits: list[float] = []
    engine = provider_pressure_engine(
        adaptive_model(),
        sleep=waits.append,
        now=lambda: datetime(2026, 11, 14, 13, 0, tzinfo=timezone.utc),
    )
    session = engine.execution_initialize(_execution_context(tmp_path, "adaptive"))
    context = _run_context(tmp_path, "adaptive")
    action = engine.action_admit(
        session,
        context,
        action_id="operation.create",
        workload=runtime_provider_pressure_workload(
            features={"operation.create": 1.0}
        ),
    )

    # Historical replay makes Event PPH pressure 2.5 (15 s), while APP
    # independently suspends the burst pocket for 30 s. They do not compete via
    # max(); the single Core actuator performs the APP suspension and then the
    # per-event PPH pacing as distinct controls.
    assert action.record["event_pph_pressure"] == 2.5
    assert action.record["event_pph_wait_seconds"] == 15.0
    assert action.record["app_suspension_seconds"] == 30.0
    assert action.record["total_admission_wait_seconds"] == 45.0
    assert waits == [30.0, 15.0]
    assert "episode" not in action.record
    assert "probe" not in action.record

    recovery = engine.recovery_actuate(
        session,
        context,
        runtime_provider_pressure_recovery_event(
            kind="secondary",
            wait_seconds=60.0,
            recommendation_source="documented_fallback",
            action_id="operation.create",
            decision_id=str(action.record["decision_id"]),
            features={"operation.create": 1.0},
        ),
    )
    assert recovery.record["underprediction_seconds"] == 45.0
    assert recovery.record["model_state_metadata"]["app_probe_state"] == "open"
    assert recovery.record["model_state_metadata"]["app_probe_id"].startswith(
        "adaptive:run=0:app="
    )

    result = engine.action_result_observe(
        session,
        context,
        runtime_provider_pressure_action_result_event(
            outcome="success",
            action_id="operation.create",
            decision_id=str(action.record["decision_id"]),
            features={"operation.create": 1.0},
        ),
    )
    assert result["model_state_metadata"]["app_probe_state"] == "closed"
    assert result["model_state_metadata"]["provider_geometry_updated"] is True
    assert session.profile["model_state"]["adaptive_pressure_pocket"][
        "closed_probe_count"
    ] == 1
    assert session.profile["model_state"]["__core_app"]["open_probes"] == {}
    assert len(session.profile["model_state"]["__core_app"]["recent_probes"]) == 1
    snapshot_path = engine.execution_finalize(session, status="completed")
    snapshot = yaml.safe_load(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["schema_version"] == "0.5"
    assert snapshot["profile_after"]["model_state"]["adaptive_pressure_pocket"][
        "closed_probe_count"
    ] == 1
    action_records = [
        item
        for item in snapshot["learning_delta"]["decisions"]
        if item.get("decision_kind") == "action"
    ]
    assert action_records[0]["event_pph_pressure"] == 2.5
    assert action_records[0]["event_pph_wait_seconds"] == 15.0
    assert action_records[0]["app_suspension_seconds"] == 30.0


def test_pph_upgrades_v04_profile_with_empty_model_state(tmp_path: Path) -> None:
    engine = provider_pressure_engine(
        fake_pressure_model(),
        sleep=lambda _seconds: None,
        now=lambda: datetime(2026, 11, 14, 13, 0, tzinfo=timezone.utc),
    )
    first = engine.execution_initialize(_execution_context(tmp_path, "first-upgrade"))
    profile_path = first.store.profile_path
    value = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    value["schema_version"] = "0.4"
    value.pop("model_state", None)
    profile_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    second = engine.execution_initialize(_execution_context(tmp_path, "second-upgrade"))
    assert second.profile["schema_version"] == "0.5"
    assert second.profile["model_state"] == {}
    persisted = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == "0.5"
    assert persisted["model_state"] == {}


def test_app_policy_supports_wildcard_action_patterns() -> None:
    policy = runtime_provider_pressure_app_policy(
        enabled=True,
        actions=("mcp.*", "controller.*", "git.push"),
    )
    assert policy.eligible("mcp.rest.read") is True
    assert policy.eligible("controller.issue.create") is True
    assert policy.eligible("git.push") is True
    assert policy.eligible("git.fetch") is False


def test_static_provider_spacing_is_independent_from_event_pph(tmp_path: Path) -> None:
    class quiet_model(fake_pressure_model):
        def historical_replay(self, context):
            return ()

    waits: list[float] = []
    engine = provider_pressure_engine(
        quiet_model(),
        sleep=waits.append,
        now=lambda: datetime(2026, 11, 14, 13, 0, tzinfo=timezone.utc),
    )
    session = engine.execution_initialize(_execution_context(tmp_path, 'static-spacing'))
    context = _run_context(tmp_path, 'static-spacing')
    workload = runtime_provider_pressure_workload(
        features={"semantic.read": 1.0},
        metadata={
            "baseline_request_interval_seconds": 1.0,
            "baseline_mutation_interval_seconds": 2.0,
            "baseline_mutation": False,
        },
    )
    first = engine.action_admit(
        session,
        context,
        action_id="controller.read",
        workload=workload,
    )
    second = engine.action_admit(
        session,
        context,
        action_id="controller.read",
        workload=workload,
    )
    assert first.record["event_pph_wait_seconds"] == 0.0
    assert first.record["baseline_spacing_wait_seconds"] == 0.0
    assert second.record["event_pph_wait_seconds"] == 0.0
    assert second.record["baseline_spacing_wait_seconds"] == 1.0
    assert waits == [1.0]


def test_selected_pph_pacing_is_reported_with_transport_details(tmp_path: Path) -> None:
    class reporting_model(fake_pressure_model):
        def historical_replay(self, context):
            return ()

        def action_console_report(self, context, action_id, workload, record):
            del context, workload, record
            return action_id == "mcp.rest.mutation"

    waits: list[float] = []
    progress: list[str] = []
    now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    engine = provider_pressure_engine(
        reporting_model(),
        sleep=waits.append,
        now=lambda: now,
    )
    session = engine.execution_initialize(_execution_context(tmp_path, "console-spacing"))
    base = _run_context(tmp_path, "console-spacing")
    context = runtime_provider_pressure_run_context(
        experiment_id=base.experiment_id,
        execution_id=base.execution_id,
        run_index=base.run_index,
        subject_id=base.subject_id,
        scenario_id=base.scenario_id,
        repetition=base.repetition,
        materialization_key=base.materialization_key,
        session_dir=base.session_dir,
        progress=progress.append,
    )
    workload = runtime_provider_pressure_workload(
        metadata={
            "baseline_request_interval_seconds": 1.0,
            "baseline_mutation_interval_seconds": 2.0,
            "baseline_mutation": True,
            "transport_plane": "subject_mcp",
            "transport_method": "PATCH",
            "transport_path": "/repos/example/demo/contents/file.txt",
        },
    )

    engine.action_admit(
        session,
        context,
        action_id="mcp.rest.mutation",
        workload=workload,
    )
    engine.action_admit(
        session,
        context,
        action_id="mcp.rest.mutation",
        workload=workload,
    )

    assert waits == [2.0]
    assert len(progress) == 2
    assert "mcp.rest.mutation admitted; wait ~0s" in progress[0]
    assert "mcp.rest.mutation admitted; wait ~2s" in progress[1]
    assert "subject_mcp PATCH /repos/example/demo/contents/file.txt" in progress[1]
    assert "provider-baseline-spacing" in progress[1]
    assert "baseline=2s" in progress[1]
