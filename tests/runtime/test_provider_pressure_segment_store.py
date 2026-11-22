from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from acmm.analysis.provider_pressure_export import export_provider_pressure
from acmm.runtime.provider_pressure_migration import (
    provider_pressure_migration_error,
    upgrade_provider_pressure_root,
)
from acmm.runtime.provider_pressure_segment_store import (
    DEFAULT_SEGMENT_POLICY_PRESET,
    SEGMENT_POLICY_PRESETS,
    STORE_DIR_NAME,
    provider_pressure_store_v2_error,
    segmented_provider_pressure_event_writer,
    segmented_provider_pressure_store,
)


IDENTITY = {
    "provider_id": "github",
    "scope_id": "scope:test",
    "model_id": "github_pressure_v1",
    "model_version": "0.13",
    "model_definition_sha256": "a" * 64,
}
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def _profile_projection(sequence: int) -> dict[str, object]:
    return {
        "sequence": sequence,
        "updated_at": "2026-11-22T12:00:00Z",
        "features": {
            "mcp.rest.read": {
                "score": float(sequence),
                "observations": sequence,
                "rewards": 0,
                "punishments": sequence,
                "confidence": 0.1,
                "runtime_pressure_events": sequence,
                "runtime_pressure_seconds": float(sequence),
                "pph_actuation_events": 0,
                "pph_actuation_seconds": 0.0,
            }
        },
        "model_state": {},
        "totals": {
            "observations": sequence,
            "reward_events": 0,
            "punishment_events": sequence,
            "runtime_pressure_events": sequence,
            "runtime_pressure_seconds": float(sequence),
            "pph_pacing_events": 0,
            "pph_pacing_seconds": 0.0,
        },
    }


def _operational_profile(sequence: int) -> dict[str, object]:
    projection = _profile_projection(sequence)
    return {
        "schema_version": "0.5",
        "identity": dict(IDENTITY),
        "sequence": sequence,
        "created_at": "2026-11-22T12:00:00Z",
        "updated_at": projection["updated_at"],
        "features": projection["features"],
        "model_state": projection["model_state"],
        "totals": projection["totals"],
        "applied_sources": ["historical_replay:prior"],
    }


def _legacy_snapshot(
    sequence: int,
    parent: str | None,
    before: dict[str, object],
    after: dict[str, object],
    *,
    event_count: int = 3,
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "0.5",
        "snapshot": {
            "sequence": sequence,
            "created_at": "2026-11-22T12:00:00Z",
            "source_kind": "bootstrap" if sequence == 0 else "live_execution",
            "source_execution_id": "bootstrap" if sequence == 0 else f"execution-{sequence}",
            "execution_status": "bootstrap" if sequence == 0 else "completed",
            "parent_sha256": parent,
        },
        "identity": dict(IDENTITY),
        "source_metadata": {
            "experiment_id": "example_v1",
            "package_sha256": "b" * 64,
            "selected_run_indices": [0],
            "session_dir": "session/example",
            "pacing_events": [],
        },
        "profile_before": before,
        "learning_delta": {
            "signals": [
                {"kind": "punishment", "count": 1, "features": {"mcp.rest.read": 1.0}}
                for _ in range(event_count)
            ],
            "decisions": [
                {
                    "decision_kind": "action",
                    "decision_id": f"d-{sequence}-{i}",
                    "action_id": "mcp.rest.read",
                    "run_index": 0,
                    "subject_id": "subject_a",
                    "scenario_id": "scenario_a",
                    "metadata": {
                        "app_suspension": {
                            "metadata": {
                                "cold_start_prior": {
                                    "utilization": {
                                        "content_requests_per_hour": 0.2,
                                        "content_requests_per_minute": 0.3,
                                    }
                                }
                            }
                        }
                    },
                }
                for i in range(event_count)
            ],
            "pacing_events": [
                {"source": "pph", "reason": "learned_provider_pressure", "duration_seconds": 1.0}
                for _ in range(event_count)
            ],
        },
        "profile_after": after,
    }
    payload = yaml.safe_dump(body, sort_keys=True, allow_unicode=False).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    body["snapshot"]["snapshot_sha256"] = digest  # type: ignore[index]
    return body


def _write_legacy_lineage(
    root: Path,
    *,
    snapshots: int = 2,
    event_count: int = 3,
    with_aliases: bool = False,
) -> Path:
    lineage = root / "provider_pressure" / "github" / "scopehash" / "github_pressure_v1" / "0.13-aaaaaaaaaaaa"
    trajectory = lineage / "trajectory"
    trajectory.mkdir(parents=True)
    entries: list[dict[str, object]] = []
    parent = None
    before = _profile_projection(0)
    if with_aliases:
        before["model_state"] = {"shared": {"window": [1, 2, 3]}}
    for sequence in range(snapshots):
        after = _profile_projection(sequence + 1)
        if with_aliases:
            after["model_state"] = before["model_state"]
        snapshot = _legacy_snapshot(sequence, parent, before, after, event_count=event_count)
        name = f"{sequence:06d}_snapshot.yaml"
        path = trajectory / name
        path.write_text(yaml.safe_dump(snapshot, sort_keys=False), encoding="utf-8")
        meta = snapshot["snapshot"]
        entries.append(
            {
                "sequence": sequence,
                "path": f"trajectory/{name}",
                "created_at": meta["created_at"],  # type: ignore[index]
                "source_kind": meta["source_kind"],  # type: ignore[index]
                "source_execution_id": meta["source_execution_id"],  # type: ignore[index]
                "snapshot_sha256": meta["snapshot_sha256"],  # type: ignore[index]
                "parent_sha256": parent,
            }
        )
        parent = str(meta["snapshot_sha256"])  # type: ignore[index]
        before = after
    index = {
        "schema_version": "0.1",
        "identity": dict(IDENTITY),
        "latest_sequence": snapshots - 1,
        "latest_snapshot": entries[-1]["path"],
        "latest_snapshot_sha256": entries[-1]["snapshot_sha256"],
        "snapshots": entries,
    }
    (lineage / "trajectory-index.yaml").write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
    (lineage / "profile-state.yaml").write_text(
        yaml.safe_dump(_operational_profile(snapshots), sort_keys=False),
        encoding="utf-8",
    )
    return lineage


def test_segment_store_rotates_and_recovers_truncated_tail(tmp_path: Path) -> None:
    lineage = tmp_path / "lineage"
    store = segmented_provider_pressure_store(lineage, IDENTITY, segment_policy_preset="small")
    store.initialize_for_migration(NOW)
    small_max_bytes, small_max_records = SEGMENT_POLICY_PRESETS["small"]
    for index in range(small_max_records + 7):
        store.append_event(
            kind="decision",
            execution_id="execution-a",
            payload={"decision_id": str(index), "padding": "x" * 32},
            created_at=NOW,
        )

    segments = sorted((lineage / STORE_DIR_NAME / "segments").glob("segment_*.jsonl"))
    assert len(segments) >= 2
    assert all(path.stat().st_size <= small_max_bytes for path in segments)
    assert [event.sequence for event in store.iter_events()] == list(range(small_max_records + 7))

    active = segments[-1]
    with active.open("ab") as handle:
        handle.write(b'{"interrupted":')
    store.initialize_for_migration(NOW)
    assert active.read_bytes().endswith(b"\n")
    assert len(list(store.iter_events())) == small_max_records + 7


def test_new_store_persists_balanced_segment_policy_and_rejects_policy_change(tmp_path: Path) -> None:
    lineage = tmp_path / "lineage"
    store = segmented_provider_pressure_store(lineage, IDENTITY)
    store.initialize_for_migration(NOW)
    document = json.loads((lineage / STORE_DIR_NAME / "lineage.json").read_text(encoding="utf-8"))
    max_bytes, max_records = SEGMENT_POLICY_PRESETS[DEFAULT_SEGMENT_POLICY_PRESET]
    assert document["segment_policy"] == {
        "preset": DEFAULT_SEGMENT_POLICY_PRESET,
        "max_bytes": max_bytes,
        "max_records": max_records,
    }
    store.append_event(
        kind="decision",
        execution_id="execution-a",
        payload={"decision_id": "1"},
        created_at=NOW,
    )
    incompatible = segmented_provider_pressure_store(
        lineage,
        IDENTITY,
        segment_policy_preset="large",
    )
    with pytest.raises(provider_pressure_store_v2_error, match="already uses segment policy"):
        incompatible.initialize_for_migration(NOW)


def test_legacy_upgrade_reports_segment_progress_and_persists_requested_policy(tmp_path: Path) -> None:
    lineage = _write_legacy_lineage(tmp_path, snapshots=1, event_count=100)
    messages: list[str] = []
    result = upgrade_provider_pressure_root(
        tmp_path / "provider_pressure",
        segment_size="small",
        progress=messages.append,
    )
    assert result.lineages[0].event_count == 300
    assert any("sealed segment" in message for message in messages)
    assert any("checkpointed legacy snapshot 0" in message for message in messages)
    document = json.loads((lineage / STORE_DIR_NAME / "lineage.json").read_text(encoding="utf-8"))
    assert document["segment_policy"]["preset"] == "small"


def test_legacy_upgrade_accepts_yaml_aliases(tmp_path: Path) -> None:
    lineage = _write_legacy_lineage(
        tmp_path,
        snapshots=2,
        event_count=2,
        with_aliases=True,
    )
    snapshot_text = (lineage / "trajectory" / "000000_snapshot.yaml").read_text(encoding="utf-8")
    assert "&id" in snapshot_text
    assert "*id" in snapshot_text

    result = upgrade_provider_pressure_root(tmp_path / "provider_pressure")
    migrated = result.lineages[0]
    assert migrated.snapshot_count == 2
    assert migrated.checkpoint_count == 2
    assert migrated.event_count == 12

    store = segmented_provider_pressure_store(lineage, IDENTITY)
    checkpoints = list(store.iter_checkpoints())
    assert len(checkpoints) == 2
    assert checkpoints[-1].profile["model_state"] == {"shared": {"window": [1, 2, 3]}}

def test_legacy_upgrade_is_restart_safe_and_exports_segmented_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lineage = _write_legacy_lineage(tmp_path, snapshots=2, event_count=3)
    original = segmented_provider_pressure_event_writer.append_event
    calls = 0

    def interrupted(self, **kwargs):
        nonlocal calls
        calls += 1
        result = original(self, **kwargs)
        if calls == 2:
            raise RuntimeError("simulated process loss")
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(segmented_provider_pressure_event_writer, "append_event", interrupted)
        with pytest.raises(RuntimeError, match="simulated process loss"):
            upgrade_provider_pressure_root(tmp_path / "provider_pressure")

    result = upgrade_provider_pressure_root(tmp_path / "provider_pressure")
    migrated = result.lineages[0]
    assert migrated.snapshot_count == 2
    assert migrated.checkpoint_count == 2
    assert migrated.event_count == 18
    store = segmented_provider_pressure_store(lineage, IDENTITY)
    events = list(store.iter_events())
    assert len(events) == 18
    assert [event.sequence for event in events] == list(range(18))
    profile = yaml.safe_load((lineage / STORE_DIR_NAME / "profile-state.yaml").read_text(encoding="utf-8"))
    assert profile["schema_version"] == "0.6"
    assert "applied_sources" not in profile
    assert store.source_applied("historical_replay:prior")

    exported = export_provider_pressure(
        tmp_path / "provider_pressure",
        tmp_path / "exports",
    )
    manifest = yaml.safe_load(exported.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "0.6"
    assert manifest["source"]["storage_kind"] == "segmented"
    assert manifest["tables"]["snapshots"]["rows"] == 2
    assert manifest["tables"]["signals"]["rows"] == 6
    assert manifest["tables"]["decisions"]["rows"] == 6
    assert manifest["tables"]["pacing_events"]["rows"] == 6
    assert manifest["tables"]["utilization_summary"]["rows"] == 2
