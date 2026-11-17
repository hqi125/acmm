from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from acmm.runtime.provider_pressure import (
    provider_pressure_engine,
    runtime_provider_pressure_app_suspension,
    runtime_provider_pressure_app_policy,
    runtime_provider_pressure_execution_context,
    runtime_provider_pressure_identity,
    runtime_provider_pressure_observation,
    runtime_provider_pressure_policy,
    runtime_provider_pressure_recovery_event,
    runtime_provider_pressure_run_context,
    runtime_provider_pressure_transport_action,
    runtime_provider_pressure_transport_recovery,
    runtime_provider_pressure_workload,
)
from acmm.runtime.provider_pressure_gate import provider_pressure_transport_gate


class gate_model:
    def identity(self, context):
        return runtime_provider_pressure_identity(
            provider_id="example",
            scope_id="example-scope",
            model_id="gate-test",
            model_version="1",
            model_definition_sha256="a" * 64,
        )

    def policy(self, context):
        return runtime_provider_pressure_policy(
            base_delay_seconds=10.0,
            max_delay_seconds=120.0,
        )

    def app_policy(self, context):
        return runtime_provider_pressure_app_policy(
            enabled=True,
            actions=("mcp.rest.read",),
        )

    def app_admission_suspension(self, context, action_id, workload, model_state):
        return runtime_provider_pressure_app_suspension(
            duration_seconds=0.0,
            metadata={"eligible": True, "usage_vector": {"requests": 3.0}},
        )

    def historical_replay(self, context):
        return ()

    def run_workload(self, context):
        return runtime_provider_pressure_workload()

    def run_measurement_start(self, context):
        return None

    def pacing_observe(self, context, event):
        return runtime_provider_pressure_observation(
            source_kind="live_execution",
            source_execution_id=context.execution_id,
        )

    def recovery_observe(self, context, event):
        return runtime_provider_pressure_observation(
            source_kind="live_execution",
            source_execution_id=context.execution_id,
        )

    def run_observe(self, context, measurement_handle, outcome):
        return runtime_provider_pressure_observation(
            source_kind="live_execution",
            source_execution_id=context.execution_id,
        )

    def transport_action(self, context, request):
        return runtime_provider_pressure_transport_action(
            action_id="mcp.rest.read",
            workload=runtime_provider_pressure_workload(
                features={"provider.example.shared": 1.0}
            ),
        )

    def transport_recovery(self, context, request, response):
        if response.status == 429 and int(response.metadata.get("attempt", 0)) < 1:
            return runtime_provider_pressure_transport_recovery(
                kind="secondary",
                wait_seconds=30.0,
                recommendation_source="documented_fallback",
                retry=True,
            )
        return None

    def app_probe_recovery_metadata(self, context, event, probe, model_state):
        return {"usage_vector": {"requests": 4.0}}

    def app_probe_close(self, context, event, probe, model_state):
        model_state["provider_geometry"] = {"closed": True}
        return {"geometry_closed": True}

    def app_safe_observe(self, context, event, model_state):
        return {"safe": True}


def execution_context(tmp_path: Path):
    session_dir = tmp_path / "results" / "execution-test"
    session_dir.mkdir(parents=True)
    return runtime_provider_pressure_execution_context(
        experiment_id="test",
        execution_id="execution-test",
        selected_run_indices=(0,),
        session_dir=str(session_dir),
        generated_root=str(tmp_path),
    )


def run_context(tmp_path: Path):
    return runtime_provider_pressure_run_context(
        experiment_id="test",
        execution_id="execution-test",
        run_index=0,
        subject_id="subject",
        scenario_id="scenario",
        repetition=1,
        materialization_key="scenario_id=scenario",
        session_dir=str(tmp_path / "results" / "execution-test"),
    )


def test_transport_gate_correlates_one_admission_across_recovery_retry(tmp_path: Path) -> None:
    waits: list[float] = []
    engine = provider_pressure_engine(
        gate_model(),
        sleep=waits.append,
        now=lambda: datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc),
    )
    session = engine.execution_initialize(execution_context(tmp_path))
    from acmm.runtime.provider_pressure import runtime_provider_pressure_gate_configuration

    gate = provider_pressure_transport_gate(
        engine,
        session,
        runtime_provider_pressure_gate_configuration(
            control_path=str(tmp_path / "pph-gate.json"),
            bind_host="127.0.0.1",
            bind_port=8765,
            advertised_url="http://127.0.0.1:8765",
        ),
    )
    gate.activate(run_context(tmp_path))

    admission = gate._admit(
        {
            "request_id": "request-1",
            "plane": "subject_mcp",
            "method": "GET",
            "url": "https://api.example.test/items",
            "request_kind": "rest",
        }
    )
    decision_id = admission["decision_id"]

    recovery = gate._feedback(
        {
            "request_id": "request-1",
            "status": 429,
            "headers": {},
            "body": "secondary rate limit",
            "metadata": {"attempt": 0},
        }
    )
    assert recovery["retry"] is True
    assert recovery["decision_id"] == decision_id
    assert waits == [30.0]
    core_app = session.profile["model_state"]["__core_app"]
    assert len(core_app["open_probes"]) == 1
    probe = next(iter(core_app["open_probes"].values()))
    assert len(probe["recovery_events"]) == 1

    result = gate._feedback(
        {
            "request_id": "request-1",
            "status": 200,
            "headers": {},
            "body": "",
            "metadata": {"attempt": 1},
        }
    )
    assert result["retry"] is False
    assert result["decision_id"] == decision_id
    assert core_app["open_probes"] == {}
    assert len(core_app["recent_probes"]) == 1
    assert session.profile["model_state"]["provider_geometry"]["closed"] is True


def test_transport_gate_treats_terminal_http_failure_as_action_failure(tmp_path: Path) -> None:
    engine = provider_pressure_engine(
        gate_model(),
        sleep=lambda seconds: None,
        now=lambda: datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc),
    )
    session = engine.execution_initialize(execution_context(tmp_path))
    from acmm.runtime.provider_pressure import runtime_provider_pressure_gate_configuration

    gate = provider_pressure_transport_gate(
        engine,
        session,
        runtime_provider_pressure_gate_configuration(
            control_path=str(tmp_path / "pph-gate.json"),
            bind_host="127.0.0.1",
            bind_port=8765,
            advertised_url="http://127.0.0.1:8765",
        ),
    )
    gate.activate(run_context(tmp_path))
    gate._admit(
        {
            "request_id": "request-2",
            "plane": "subject_mcp",
            "method": "GET",
            "url": "https://api.example.test/items",
            "request_kind": "rest",
        }
    )
    result = gate._feedback(
        {
            "request_id": "request-2",
            "status": 403,
            "headers": {},
            "body": "ordinary forbidden",
            "metadata": {"attempt": 0},
        }
    )
    assert result["retry"] is False
    assert result["result"]["outcome"] == "failure"
