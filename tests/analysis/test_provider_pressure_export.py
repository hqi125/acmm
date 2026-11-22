from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from acmm.analysis.export import analysis_export_error
from acmm.analysis.provider_pressure_export import export_provider_pressure


IDENTITY = {
    "provider_id": "github",
    "scope_id": "scope:test",
    "model_id": "github_pressure_v1",
    "model_version": "0.13",
    "model_definition_sha256": "a" * 64,
}


def _feature(score: float, observations: int = 1) -> dict[str, object]:
    return {
        "score": score,
        "observations": observations,
        "rewards": 0,
        "punishments": 1,
        "confidence": 0.1,
        "runtime_pressure_events": 1,
        "runtime_pressure_seconds": 30.0,
        "pph_actuation_events": 1,
        "pph_actuation_seconds": 5.0,
    }


def _totals(observations: int, punishments: int, runtime_seconds: float, pph_seconds: float) -> dict[str, object]:
    return {
        "observations": observations,
        "reward_events": 0,
        "punishment_events": punishments,
        "runtime_pressure_events": punishments,
        "runtime_pressure_seconds": runtime_seconds,
        "pph_pacing_events": observations,
        "pph_pacing_seconds": pph_seconds,
    }


def _snapshot(sequence: int, parent: str | None, source_kind: str, execution_id: str) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "0.5",
        "snapshot": {
            "sequence": sequence,
            "created_at": f"2026-11-21T00:00:0{sequence}Z",
            "source_kind": source_kind,
            "source_execution_id": execution_id,
            "execution_status": "completed" if source_kind == "live_execution" else source_kind,
            "parent_sha256": parent,
        },
        "identity": dict(IDENTITY),
        "source_metadata": {
            "experiment_id": "example_v1",
            "package_sha256": "b" * 64,
            "selected_run_indices": [0, 1],
            "session_dir": "session/example",
            "pacing_events": [
                {
                    "source": "pph",
                    "reason": "learned_provider_pressure",
                    "duration_seconds": 5.0,
                    "action_id": "mcp.rest.read",
                    "features": {"provider.github.shared_pressure": 1.0},
                    "metadata": {
                        "run_index": 0,
                        "subject_id": "subject_a",
                        "scenario_id": "scenario_a",
                        "decision_id": "d1",
                    },
                }
            ],
        },
        "profile_before": {
            "sequence": sequence,
            "updated_at": "2026-11-21T00:00:00Z",
            "features": {
                "provider.github.shared_pressure": _feature(1.0 if sequence == 0 else 2.0),
            },
            "model_state": {"adaptive_pressure_pocket": {"probe_count": sequence}},
            "totals": _totals(sequence, sequence, sequence * 30.0, sequence * 5.0),
        },
        "learning_delta": {
            "signals": [
                {
                    "kind": "punishment",
                    "count": 1,
                    "severity": 1.0,
                    "pressure_duration_seconds": 30.0,
                    "features": {
                        "provider.github.shared_pressure": {
                            "weight": 1.0,
                            "score_before": 1.0,
                            "score_after": 2.0,
                            "runtime_pressure_seconds": 30.0,
                        }
                    },
                    "metadata": {"reason": "provider_recovery"},
                }
            ] if sequence else [],
            "decisions": [
                {
                    "decision_kind": "action",
                    "run_index": 0,
                    "subject_id": "subject_a",
                    "scenario_id": "scenario_a",
                    "action_id": "mcp.rest.read",
                    "decision_id": "d1",
                    "effective_pressure": 2.0,
                    "event_pph_pressure": 2.0,
                    "event_pph_wait_seconds": 5.0,
                    "app_suspension_seconds": 0.0,
                    "total_admission_wait_seconds": 5.0,
                    "recommended_wait_seconds": 5.0,
                    "features": {"provider.github.shared_pressure": 1.0},
                    "metadata": {},
                }
            ] if sequence else [],
            "pacing_events": [
                {
                    "source": "pph",
                    "reason": "learned_provider_pressure",
                    "duration_seconds": 5.0,
                    "action_id": "mcp.rest.read",
                    "features": {"provider.github.shared_pressure": 1.0},
                    "metadata": {
                        "run_index": 0,
                        "subject_id": "subject_a",
                        "scenario_id": "scenario_a",
                        "decision_id": "d1",
                    },
                }
            ] if sequence else [],
        },
        "profile_after": {
            "sequence": sequence + 1,
            "updated_at": "2026-11-21T00:00:01Z",
            "features": {
                "provider.github.shared_pressure": _feature(1.0 if sequence == 0 else 2.5),
            },
            "model_state": {"adaptive_pressure_pocket": {"probe_count": sequence + 1}},
            "totals": _totals(sequence + 1, sequence + 1, (sequence + 1) * 30.0, (sequence + 1) * 5.0),
        },
    }
    payload = yaml.safe_dump(body, sort_keys=True, allow_unicode=False).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    body["snapshot"]["snapshot_sha256"] = digest  # type: ignore[index]
    return body


def _write_lineage(root: Path, snapshot_count: int = 2) -> Path:
    lineage = root / "provider_pressure" / "github" / "scopehash" / "github_pressure_v1" / "0.13-aaaaaaaaaaaa"
    trajectory = lineage / "trajectory"
    trajectory.mkdir(parents=True)
    snapshots = []
    parent = None
    for sequence in range(snapshot_count):
        source_kind = "bootstrap" if sequence == 0 else "live_execution"
        execution_id = "bootstrap" if sequence == 0 else f"execution-{sequence}"
        snapshot = _snapshot(sequence, parent, source_kind, execution_id)
        meta = snapshot["snapshot"]
        filename = f"{sequence:06d}_snapshot.yaml"
        (trajectory / filename).write_text(yaml.safe_dump(snapshot, sort_keys=False), encoding="utf-8")
        snapshots.append(
            {
                "sequence": sequence,
                "path": f"trajectory/{filename}",
                "created_at": meta["created_at"],
                "source_kind": meta["source_kind"],
                "source_execution_id": meta["source_execution_id"],
                "snapshot_sha256": meta["snapshot_sha256"],
                "parent_sha256": parent,
            }
        )
        parent = meta["snapshot_sha256"]
    index = {
        "schema_version": "0.1",
        "identity": dict(IDENTITY),
        "latest_sequence": snapshot_count - 1,
        "latest_snapshot": snapshots[-1]["path"],
        "latest_snapshot_sha256": snapshots[-1]["snapshot_sha256"],
        "snapshots": snapshots,
    }
    (lineage / "trajectory-index.yaml").write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
    return lineage


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_provider_pressure_export_validates_chain_and_normalizes_tables(tmp_path: Path) -> None:
    lineage = _write_lineage(tmp_path)
    messages: list[str] = []
    result = export_provider_pressure(
        tmp_path / "provider_pressure",
        tmp_path / "exports",
        progress=messages.append,
    )

    assert result.lineage_rows == 1
    assert result.snapshot_rows == 2
    assert result.feature_rows == 2
    assert result.signal_rows == 1
    assert result.decision_rows == 1
    assert result.pacing_event_rows == 1
    assert result.output_dir.parent.name == "provider_pressure"
    assert messages[0].startswith("discovering")
    assert messages[-1].startswith("publishing")

    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "0.2"
    assert manifest["source"]["kind"] == "provider_pressure"
    assert manifest["source"]["lineage_count"] == 1
    assert manifest["source"]["snapshot_count"] == 2
    assert manifest["source"]["trajectory_schema_versions"] == ["0.5"]
    assert manifest["tables"]["snapshots"]["rows"] == 2

    snapshots = _rows(result.snapshots_path)
    assert snapshots[1]["source_execution_id"] == "execution-1"
    assert snapshots[1]["runtime_pressure_seconds_delta"] == "30.0"
    assert json.loads(snapshots[1]["model_state_after_json"])["adaptive_pressure_pocket"]["probe_count"] == 2

    features = _rows(result.features_path)
    assert features[1]["feature"] == "provider.github.shared_pressure"
    assert features[1]["score_delta"] == "0.5"

    pacing = _rows(result.pacing_events_path)
    assert pacing[0]["reason"] == "learned_provider_pressure"
    assert pacing[0]["subject_id"] == "subject_a"

    second_root = tmp_path / "exports2"
    second = export_provider_pressure(lineage, second_root)
    assert second.export_id == result.export_id
    assert second.snapshots_path.read_bytes() == result.snapshots_path.read_bytes()
    assert second.manifest_path.read_bytes() == result.manifest_path.read_bytes()


def test_provider_pressure_export_rejects_tampered_snapshot(tmp_path: Path) -> None:
    lineage = _write_lineage(tmp_path)
    snapshot_path = lineage / "trajectory" / "000001_snapshot.yaml"
    snapshot = yaml.safe_load(snapshot_path.read_text(encoding="utf-8"))
    snapshot["profile_after"]["totals"]["runtime_pressure_seconds"] = 999.0
    snapshot_path.write_text(yaml.safe_dump(snapshot, sort_keys=False), encoding="utf-8")

    with pytest.raises(analysis_export_error, match="snapshot digest mismatch"):
        export_provider_pressure(tmp_path / "provider_pressure", tmp_path / "exports")


