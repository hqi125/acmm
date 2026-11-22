from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from jsonschema import Draft202012Validator
import yaml

from acmm.analysis.export import (
    analysis_export_error,
    _csv_table_writer,
    _json_cell,
    _staged_export_directory,
    _write_export_bytes,
)


PROVIDER_PRESSURE_EXPORT_SCHEMA_VERSION = "0.2"

LINEAGE_COLUMNS = (
    "export_schema_version",
    "lineage_id",
    "provider_id",
    "scope_id",
    "model_id",
    "model_version",
    "model_definition_sha256",
    "snapshot_count",
    "latest_sequence",
    "latest_snapshot_sha256",
)

SNAPSHOT_COLUMNS = (
    "export_schema_version",
    "lineage_id",
    "provider_id",
    "scope_id",
    "model_id",
    "model_version",
    "model_definition_sha256",
    "trajectory_schema_version",
    "sequence",
    "created_at",
    "source_kind",
    "source_execution_id",
    "execution_status",
    "snapshot_sha256",
    "parent_sha256",
    "experiment_id",
    "package_sha256",
    "selected_run_count",
    "session_dir",
    "observations_before",
    "observations_after",
    "observations_delta",
    "reward_events_before",
    "reward_events_after",
    "reward_events_delta",
    "punishment_events_before",
    "punishment_events_after",
    "punishment_events_delta",
    "runtime_pressure_events_before",
    "runtime_pressure_events_after",
    "runtime_pressure_events_delta",
    "runtime_pressure_seconds_before",
    "runtime_pressure_seconds_after",
    "runtime_pressure_seconds_delta",
    "pph_pacing_events_before",
    "pph_pacing_events_after",
    "pph_pacing_events_delta",
    "pph_pacing_seconds_before",
    "pph_pacing_seconds_after",
    "pph_pacing_seconds_delta",
    "signal_count",
    "decision_count",
    "pacing_event_count",
    "source_metadata_json",
    "model_state_before_json",
    "model_state_after_json",
)

FEATURE_COLUMNS = (
    "export_schema_version",
    "lineage_id",
    "sequence",
    "created_at",
    "source_kind",
    "source_execution_id",
    "feature",
    "score_before",
    "score_after",
    "score_delta",
    "observations_after",
    "rewards_after",
    "punishments_after",
    "confidence_after",
    "runtime_pressure_events_after",
    "runtime_pressure_seconds_after",
    "pph_actuation_events_after",
    "pph_actuation_seconds_after",
    "state_before_json",
    "state_after_json",
)

SIGNAL_COLUMNS = (
    "export_schema_version",
    "lineage_id",
    "sequence",
    "created_at",
    "source_kind",
    "source_execution_id",
    "signal_index",
    "kind",
    "count",
    "severity",
    "pressure_duration_seconds",
    "features_json",
    "metadata_json",
    "observation_metadata_json",
    "record_json",
)

DECISION_COLUMNS = (
    "export_schema_version",
    "lineage_id",
    "sequence",
    "created_at",
    "source_execution_id",
    "decision_index",
    "decision_kind",
    "run_index",
    "subject_id",
    "scenario_id",
    "action_id",
    "decision_id",
    "outcome",
    "effective_pressure",
    "event_pph_pressure",
    "event_pph_wait_seconds",
    "app_suspension_seconds",
    "baseline_spacing_wait_seconds",
    "total_admission_wait_seconds",
    "recommended_wait_seconds",
    "provider_wait_seconds",
    "underprediction_seconds",
    "features_json",
    "metadata_json",
    "model_state_metadata_json",
    "record_json",
)

PACING_COLUMNS = (
    "export_schema_version",
    "lineage_id",
    "sequence",
    "created_at",
    "source_execution_id",
    "pacing_index",
    "source",
    "reason",
    "duration_seconds",
    "action_id",
    "run_index",
    "subject_id",
    "scenario_id",
    "decision_id",
    "features_json",
    "metadata_json",
)

_TOTAL_FIELDS = (
    "observations",
    "reward_events",
    "punishment_events",
    "runtime_pressure_events",
    "runtime_pressure_seconds",
    "pph_pacing_events",
    "pph_pacing_seconds",
)


@dataclass(frozen=True)
class provider_pressure_export_result:
    export_id: str
    output_dir: Path
    lineages_path: Path
    snapshots_path: Path
    features_path: Path
    signals_path: Path
    decisions_path: Path
    pacing_events_path: Path
    manifest_path: Path
    lineage_rows: int
    snapshot_rows: int
    feature_rows: int
    signal_rows: int
    decision_rows: int
    pacing_event_rows: int


@dataclass(frozen=True)
class _lineage:
    lineage_id: str
    root: Path
    identity: dict[str, str]
    index: dict[str, Any]
    entries: tuple[dict[str, Any], ...]


def export_provider_pressure(
    pressure_root: str | Path,
    output_root: str | Path,
    progress: Callable[[str], None] | None = None,
) -> provider_pressure_export_result:
    source_root = Path(pressure_root).expanduser().resolve()
    if not source_root.is_dir():
        raise analysis_export_error(
            f"Provider Pressure root does not exist: '{source_root}'."
        )

    _progress(progress, "discovering Provider Pressure trajectory lineages...")
    lineage_roots = _discover_lineage_roots(source_root)
    if not lineage_roots:
        raise analysis_export_error(
            f"No trajectory-index.yaml files were found under '{source_root}'."
        )

    _progress(
        progress,
        f"loading {len(lineage_roots)} trajectory index(es)...",
    )
    lineages = tuple(
        sorted(
            (_load_lineage_index(path) for path in lineage_roots),
            key=lambda item: item.lineage_id,
        )
    )
    lineage_ids = [item.lineage_id for item in lineages]
    if len(set(lineage_ids)) != len(lineage_ids):
        raise analysis_export_error(
            "Provider Pressure root contains duplicate lineage identities."
        )

    snapshot_count = sum(len(item.entries) for item in lineages)
    source_digest = _source_digest(lineages)
    export_id = source_digest[:16]
    output_dir = (
        Path(output_root).expanduser().resolve()
        / "provider_pressure"
        / f"export_{export_id}"
    )

    _progress(
        progress,
        (
            f"stream-validating {snapshot_count} snapshot(s) and immutable hash chains; "
            "memory is bounded to the current snapshot..."
        ),
    )

    schema_versions: set[str] = set()
    source_lineages = [
        {
            "lineage_id": item.lineage_id,
            **item.identity,
            "snapshot_count": len(item.entries),
            "latest_sequence": item.index["latest_sequence"],
            "latest_snapshot_sha256": item.index["latest_snapshot_sha256"],
        }
        for item in lineages
    ]

    with _staged_export_directory(output_dir) as staging_dir:
        with ExitStack() as stack:
            writers = {
                "lineages": stack.enter_context(
                    _csv_table_writer(staging_dir / "lineages.csv", LINEAGE_COLUMNS)
                ),
                "snapshots": stack.enter_context(
                    _csv_table_writer(staging_dir / "snapshots.csv", SNAPSHOT_COLUMNS)
                ),
                "features": stack.enter_context(
                    _csv_table_writer(staging_dir / "features.csv", FEATURE_COLUMNS)
                ),
                "signals": stack.enter_context(
                    _csv_table_writer(staging_dir / "signals.csv", SIGNAL_COLUMNS)
                ),
                "decisions": stack.enter_context(
                    _csv_table_writer(staging_dir / "decisions.csv", DECISION_COLUMNS)
                ),
                "pacing_events": stack.enter_context(
                    _csv_table_writer(staging_dir / "pacing_events.csv", PACING_COLUMNS)
                ),
            }

            for lineage in lineages:
                writers["lineages"].write(_lineage_row(lineage))

            processed = 0
            report_every = max(25, snapshot_count // 10) if snapshot_count else 1
            for lineage in lineages:
                parent: str | None = None
                for entry in lineage.entries:
                    snapshot = _load_validated_snapshot(lineage, entry, parent)
                    meta = _mapping(snapshot.get("snapshot"))
                    parent = str(meta.get("snapshot_sha256", ""))
                    schema_versions.add(str(snapshot.get("schema_version", "")))

                    writers["snapshots"].write(_snapshot_row(lineage, snapshot))
                    writers["features"].write_many(_feature_rows(lineage, snapshot))
                    writers["signals"].write_many(_signal_rows(lineage, snapshot))
                    writers["decisions"].write_many(_decision_rows(lineage, snapshot))
                    writers["pacing_events"].write_many(_pacing_rows(lineage, snapshot))

                    processed += 1
                    if snapshot_count >= 50 and processed < snapshot_count and processed % report_every == 0:
                        _progress(
                            progress,
                            f"stream-validated and normalized {processed}/{snapshot_count} snapshot(s)...",
                        )
                    del snapshot

        manifest = {
            "schema_version": PROVIDER_PRESSURE_EXPORT_SCHEMA_VERSION,
            "source": {
                "kind": "provider_pressure",
                "source_digest_sha256": source_digest,
                "lineage_count": len(lineages),
                "snapshot_count": snapshot_count,
                "trajectory_schema_versions": sorted(schema_versions),
                "lineages": source_lineages,
            },
            "tables": {
                name: writer.manifest()
                for name, writer in writers.items()
            },
        }
        manifest_bytes = yaml.safe_dump(
            manifest,
            sort_keys=False,
            allow_unicode=False,
        ).replace("\n", "\r\n").encode("utf-8")
        _write_export_bytes(staging_dir / "export_manifest.yaml", manifest_bytes)

        _progress(
            progress,
            (
                f"publishing {writers['lineages'].rows} lineage, "
                f"{writers['snapshots'].rows} snapshot, "
                f"{writers['features'].rows} feature, "
                f"{writers['signals'].rows} signal, "
                f"{writers['decisions'].rows} decision, and "
                f"{writers['pacing_events'].rows} pacing row(s)..."
            ),
        )

        counts = {name: writer.rows for name, writer in writers.items()}

    return provider_pressure_export_result(
        export_id=export_id,
        output_dir=output_dir,
        lineages_path=output_dir / "lineages.csv",
        snapshots_path=output_dir / "snapshots.csv",
        features_path=output_dir / "features.csv",
        signals_path=output_dir / "signals.csv",
        decisions_path=output_dir / "decisions.csv",
        pacing_events_path=output_dir / "pacing_events.csv",
        manifest_path=output_dir / "export_manifest.yaml",
        lineage_rows=counts["lineages"],
        snapshot_rows=counts["snapshots"],
        feature_rows=counts["features"],
        signal_rows=counts["signals"],
        decision_rows=counts["decisions"],
        pacing_event_rows=counts["pacing_events"],
    )

def _discover_lineage_roots(root: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    direct = root / "trajectory-index.yaml"
    if direct.is_file() and not direct.is_symlink():
        candidates.append(root)
    for path in root.rglob("trajectory-index.yaml"):
        if path.is_symlink() or not path.is_file():
            continue
        parent = path.parent.resolve()
        if parent not in candidates:
            candidates.append(parent)
    return tuple(sorted(candidates, key=lambda item: item.as_posix()))


def _load_lineage_index(root: Path) -> _lineage:
    index_path = root / "trajectory-index.yaml"
    index = _load_yaml_mapping(index_path, "Provider Pressure trajectory index")
    _validate_schema("provider-pressure-index-v0.1.schema.json", index)
    identity = _identity(index.get("identity"))
    lineage_id = _lineage_id(identity)
    raw_entries = index.get("snapshots")
    if not isinstance(raw_entries, list):
        raise analysis_export_error(
            f"Provider Pressure index '{index_path}' has no snapshot list."
        )

    entries: list[dict[str, Any]] = []
    parent: str | None = None
    for expected_sequence, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, Mapping):
            raise analysis_export_error(
                f"Provider Pressure index '{index_path}' contains a malformed snapshot entry."
            )
        entry = dict(raw_entry)
        sequence = int(entry.get("sequence", -1))
        if sequence != expected_sequence:
            raise analysis_export_error(
                f"Provider Pressure lineage '{root}' has non-contiguous sequence {sequence}; expected {expected_sequence}."
            )
        relative = Path(str(entry.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise analysis_export_error(
                f"Provider Pressure snapshot path is unsafe in '{index_path}': '{relative}'."
            )
        snapshot_path = root / relative
        if not snapshot_path.is_file() or snapshot_path.is_symlink():
            raise analysis_export_error(
                f"Provider Pressure snapshot does not exist safely: '{snapshot_path}'."
            )
        if entry.get("parent_sha256") != parent:
            raise analysis_export_error(
                f"Provider Pressure index hash chain mismatch at sequence {sequence} in '{root}'."
            )
        digest = entry.get("snapshot_sha256")
        if not isinstance(digest, str) or not digest:
            raise analysis_export_error(
                f"Provider Pressure index snapshot digest is missing at sequence {sequence} in '{root}'."
            )
        entries.append(entry)
        parent = digest

    expected_latest_sequence = len(entries) - 1
    if int(index.get("latest_sequence", -1)) != expected_latest_sequence:
        raise analysis_export_error(
            f"Provider Pressure index latest_sequence is inconsistent in '{index_path}'."
        )
    if index.get("latest_snapshot_sha256") != parent:
        raise analysis_export_error(
            f"Provider Pressure index latest snapshot hash is inconsistent in '{index_path}'."
        )
    if entries:
        expected_path = str(entries[-1]["path"])
        if index.get("latest_snapshot") != expected_path:
            raise analysis_export_error(
                f"Provider Pressure index latest snapshot path is inconsistent in '{index_path}'."
            )
    elif index.get("latest_snapshot") is not None:
        raise analysis_export_error(
            f"Provider Pressure empty index has a latest snapshot in '{index_path}'."
        )

    return _lineage(
        lineage_id=lineage_id,
        root=root,
        identity=identity,
        index={
            "latest_sequence": index.get("latest_sequence"),
            "latest_snapshot": index.get("latest_snapshot"),
            "latest_snapshot_sha256": index.get("latest_snapshot_sha256"),
        },
        entries=tuple(entries),
    )


def _load_validated_snapshot(
    lineage: _lineage,
    entry: Mapping[str, Any],
    expected_parent: str | None,
) -> dict[str, Any]:
    relative = Path(str(entry["path"]))
    snapshot_path = lineage.root / relative
    snapshot = _load_yaml_mapping(snapshot_path, "Provider Pressure trajectory snapshot")
    schema_version = str(snapshot.get("schema_version", ""))
    if schema_version not in {"0.1", "0.2", "0.3", "0.4", "0.5"}:
        raise analysis_export_error(
            f"Unsupported Provider Pressure trajectory schema_version '{schema_version}' in '{snapshot_path}'."
        )
    _validate_schema(
        f"provider-pressure-trajectory-v{schema_version}.schema.json",
        snapshot,
    )
    if _identity(snapshot.get("identity")) != lineage.identity:
        raise analysis_export_error(
            f"Provider Pressure snapshot identity changed within lineage '{lineage.root}'."
        )
    meta = snapshot.get("snapshot")
    if not isinstance(meta, Mapping):
        raise analysis_export_error(
            f"Provider Pressure snapshot '{snapshot_path}' has no snapshot metadata."
        )
    digest = str(meta.get("snapshot_sha256", ""))
    if digest != _snapshot_document_sha256(snapshot):
        raise analysis_export_error(
            f"Provider Pressure snapshot digest mismatch: '{snapshot_path}'."
        )
    if digest != entry.get("snapshot_sha256"):
        raise analysis_export_error(
            f"Provider Pressure index digest mismatch for '{snapshot_path}'."
        )
    if meta.get("sequence") != entry.get("sequence"):
        raise analysis_export_error(
            f"Provider Pressure snapshot sequence mismatch for '{snapshot_path}'."
        )
    if meta.get("parent_sha256") != expected_parent or entry.get("parent_sha256") != expected_parent:
        raise analysis_export_error(
            f"Provider Pressure hash chain mismatch at sequence {entry.get('sequence')} in '{lineage.root}'."
        )
    if meta.get("source_kind") != entry.get("source_kind"):
        raise analysis_export_error(
            f"Provider Pressure snapshot source kind mismatch for '{snapshot_path}'."
        )
    if meta.get("source_execution_id") != entry.get("source_execution_id"):
        raise analysis_export_error(
            f"Provider Pressure snapshot execution identity mismatch for '{snapshot_path}'."
        )
    return snapshot

def _identity(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise analysis_export_error("Provider Pressure identity must be a mapping.")
    required = (
        "provider_id",
        "scope_id",
        "model_id",
        "model_version",
        "model_definition_sha256",
    )
    result: dict[str, str] = {}
    for key in required:
        item = value.get(key)
        if not isinstance(item, str) or not item:
            raise analysis_export_error(f"Provider Pressure identity.{key} must be non-empty.")
        result[key] = item
    return result


def _lineage_id(identity: Mapping[str, str]) -> str:
    payload = json.dumps(dict(identity), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _lineage_row(lineage: _lineage) -> dict[str, Any]:
    return {
        "export_schema_version": PROVIDER_PRESSURE_EXPORT_SCHEMA_VERSION,
        "lineage_id": lineage.lineage_id,
        **lineage.identity,
        "snapshot_count": len(lineage.entries),
        "latest_sequence": lineage.index["latest_sequence"],
        "latest_snapshot_sha256": lineage.index["latest_snapshot_sha256"] or "",
    }


def _snapshot_row(lineage: _lineage, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    meta = _mapping(snapshot.get("snapshot"))
    source = _mapping(snapshot.get("source_metadata"))
    before = _mapping(snapshot.get("profile_before"))
    after = _mapping(snapshot.get("profile_after"))
    before_totals = _mapping(before.get("totals"))
    after_totals = _mapping(after.get("totals"))
    row: dict[str, Any] = {
        "export_schema_version": PROVIDER_PRESSURE_EXPORT_SCHEMA_VERSION,
        "lineage_id": lineage.lineage_id,
        **lineage.identity,
        "trajectory_schema_version": snapshot.get("schema_version", ""),
        "sequence": meta.get("sequence", ""),
        "created_at": meta.get("created_at", ""),
        "source_kind": meta.get("source_kind", ""),
        "source_execution_id": meta.get("source_execution_id", ""),
        "execution_status": meta.get("execution_status", ""),
        "snapshot_sha256": meta.get("snapshot_sha256", ""),
        "parent_sha256": meta.get("parent_sha256") or "",
        "experiment_id": source.get("experiment_id", ""),
        "package_sha256": source.get("package_sha256") or "",
        "selected_run_count": len(source.get("selected_run_indices", ()))
        if isinstance(source.get("selected_run_indices"), list)
        else "",
        "session_dir": source.get("session_dir", ""),
        "signal_count": _list_length(_mapping(snapshot.get("learning_delta")).get("signals")),
        "decision_count": _list_length(_mapping(snapshot.get("learning_delta")).get("decisions")),
        "pacing_event_count": _list_length(_mapping(snapshot.get("learning_delta")).get("pacing_events")),
        "source_metadata_json": _json_cell(source),
        "model_state_before_json": _json_cell(_mapping(before.get("model_state"))),
        "model_state_after_json": _json_cell(_mapping(after.get("model_state"))),
    }
    for field in _TOTAL_FIELDS:
        before_value = _number(before_totals.get(field, 0))
        after_value = _number(after_totals.get(field, 0))
        row[f"{field}_before"] = before_value
        row[f"{field}_after"] = after_value
        row[f"{field}_delta"] = after_value - before_value
    return row


def _feature_rows(lineage: _lineage, snapshot: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    meta = _mapping(snapshot.get("snapshot"))
    before_features = _mapping(_mapping(snapshot.get("profile_before")).get("features"))
    after_features = _mapping(_mapping(snapshot.get("profile_after")).get("features"))
    names = sorted(set(before_features) | set(after_features))
    for name in names:
        before = _mapping(before_features.get(name))
        after = _mapping(after_features.get(name))
        score_before = _number(before.get("score", 0))
        score_after = _number(after.get("score", 0))
        yield {
                "export_schema_version": PROVIDER_PRESSURE_EXPORT_SCHEMA_VERSION,
                "lineage_id": lineage.lineage_id,
                "sequence": meta.get("sequence", ""),
                "created_at": meta.get("created_at", ""),
                "source_kind": meta.get("source_kind", ""),
                "source_execution_id": meta.get("source_execution_id", ""),
                "feature": name,
                "score_before": score_before,
                "score_after": score_after,
                "score_delta": score_after - score_before,
                "observations_after": _number(after.get("observations", 0)),
                "rewards_after": _number(after.get("rewards", 0)),
                "punishments_after": _number(after.get("punishments", 0)),
                "confidence_after": _number(after.get("confidence", 0)),
                "runtime_pressure_events_after": _number(after.get("runtime_pressure_events", 0)),
                "runtime_pressure_seconds_after": _number(after.get("runtime_pressure_seconds", 0)),
                "pph_actuation_events_after": _number(after.get("pph_actuation_events", 0)),
                "pph_actuation_seconds_after": _number(after.get("pph_actuation_seconds", 0)),
                "state_before_json": _json_cell(before),
                "state_after_json": _json_cell(after),
        }


def _signal_rows(lineage: _lineage, snapshot: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    meta = _mapping(snapshot.get("snapshot"))
    delta = _mapping(snapshot.get("learning_delta"))
    signals = delta.get("signals", ())
    if not isinstance(signals, list):
        return
    for index, raw in enumerate(signals, start=1):
        record = _mapping(raw)
        yield {
                "export_schema_version": PROVIDER_PRESSURE_EXPORT_SCHEMA_VERSION,
                "lineage_id": lineage.lineage_id,
                "sequence": meta.get("sequence", ""),
                "created_at": meta.get("created_at", ""),
                "source_kind": meta.get("source_kind", ""),
                "source_execution_id": meta.get("source_execution_id", ""),
                "signal_index": index,
                "kind": record.get("kind", ""),
                "count": record.get("count", ""),
                "severity": record.get("severity", ""),
                "pressure_duration_seconds": record.get("pressure_duration_seconds", ""),
                "features_json": _json_cell(_mapping(record.get("features"))),
                "metadata_json": _json_cell(_mapping(record.get("metadata"))),
                "observation_metadata_json": _json_cell(_mapping(record.get("observation_metadata"))),
                "record_json": _json_cell(record),
        }


def _decision_rows(lineage: _lineage, snapshot: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    meta = _mapping(snapshot.get("snapshot"))
    decisions = _mapping(snapshot.get("learning_delta")).get("decisions", ())
    if not isinstance(decisions, list):
        return
    for index, raw in enumerate(decisions, start=1):
        record = _mapping(raw)
        yield {
                "export_schema_version": PROVIDER_PRESSURE_EXPORT_SCHEMA_VERSION,
                "lineage_id": lineage.lineage_id,
                "sequence": meta.get("sequence", ""),
                "created_at": meta.get("created_at", ""),
                "source_execution_id": meta.get("source_execution_id", ""),
                "decision_index": index,
                "decision_kind": record.get("decision_kind", ""),
                "run_index": record.get("run_index", ""),
                "subject_id": record.get("subject_id", ""),
                "scenario_id": record.get("scenario_id", ""),
                "action_id": record.get("action_id", ""),
                "decision_id": record.get("decision_id", ""),
                "outcome": record.get("outcome", ""),
                "effective_pressure": record.get("effective_pressure", ""),
                "event_pph_pressure": record.get("event_pph_pressure", ""),
                "event_pph_wait_seconds": record.get("event_pph_wait_seconds", ""),
                "app_suspension_seconds": record.get("app_suspension_seconds", ""),
                "baseline_spacing_wait_seconds": record.get("baseline_spacing_wait_seconds", ""),
                "total_admission_wait_seconds": record.get("total_admission_wait_seconds", ""),
                "recommended_wait_seconds": record.get("recommended_wait_seconds", ""),
                "provider_wait_seconds": record.get("provider_wait_seconds", record.get("wait_seconds", "")),
                "underprediction_seconds": record.get("underprediction_seconds", ""),
                "features_json": _json_cell(_mapping(record.get("features"))),
                "metadata_json": _json_cell(_mapping(record.get("metadata"))),
                "model_state_metadata_json": _json_cell(_mapping(record.get("model_state_metadata"))),
                "record_json": _json_cell(record),
        }


def _pacing_rows(lineage: _lineage, snapshot: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    meta = _mapping(snapshot.get("snapshot"))
    pacing = _mapping(snapshot.get("learning_delta")).get("pacing_events", ())
    if not isinstance(pacing, list):
        return
    for index, raw in enumerate(pacing, start=1):
        record = _mapping(raw)
        metadata = _mapping(record.get("metadata"))
        yield {
                "export_schema_version": PROVIDER_PRESSURE_EXPORT_SCHEMA_VERSION,
                "lineage_id": lineage.lineage_id,
                "sequence": meta.get("sequence", ""),
                "created_at": meta.get("created_at", ""),
                "source_execution_id": meta.get("source_execution_id", ""),
                "pacing_index": index,
                "source": record.get("source", ""),
                "reason": record.get("reason", ""),
                "duration_seconds": record.get("duration_seconds", ""),
                "action_id": record.get("action_id", ""),
                "run_index": metadata.get("run_index", ""),
                "subject_id": metadata.get("subject_id", ""),
                "scenario_id": metadata.get("scenario_id", ""),
                "decision_id": metadata.get("decision_id", ""),
                "features_json": _json_cell(_mapping(record.get("features"))),
                "metadata_json": _json_cell(metadata),
        }


def _source_digest(lineages: Iterable[_lineage]) -> str:
    digest = hashlib.sha256()
    digest.update(b"[")
    first_lineage = True
    for lineage in lineages:
        if not first_lineage:
            digest.update(b",")
        first_lineage = False
        digest.update(b'{"identity":')
        digest.update(
            json.dumps(
                lineage.identity,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        )
        digest.update(b',"lineage_id":')
        digest.update(json.dumps(lineage.lineage_id, ensure_ascii=True).encode("utf-8"))
        digest.update(b',"snapshots":[')
        first_snapshot = True
        for entry in lineage.entries:
            if not first_snapshot:
                digest.update(b",")
            first_snapshot = False
            digest.update(b'{"sequence":')
            digest.update(str(entry["sequence"]).encode("ascii"))
            digest.update(b',"sha256":')
            digest.update(
                json.dumps(str(entry["snapshot_sha256"]), ensure_ascii=True).encode("utf-8")
            )
            digest.update(b"}")
        digest.update(b"]}")
    digest.update(b"]")
    return digest.hexdigest()


class _hash_text_sink:
    def __init__(self) -> None:
        self._hash = hashlib.sha256()

    def write(self, value: str) -> int:
        self._hash.update(value.encode("utf-8"))
        return len(value)

    def hexdigest(self) -> str:
        return self._hash.hexdigest()

def _snapshot_document_sha256(snapshot: Mapping[str, Any]) -> str:
    if not isinstance(snapshot, dict):
        raise analysis_export_error("Provider Pressure snapshot must be a mutable mapping.")
    meta = snapshot.get("snapshot")
    if not isinstance(meta, dict):
        raise analysis_export_error("Provider Pressure snapshot metadata must be a mapping.")
    digest = meta.pop("snapshot_sha256", None)
    try:
        sink = _hash_text_sink()
        yaml.safe_dump(
            snapshot,
            stream=sink,
            sort_keys=True,
            allow_unicode=False,
        )
        return sink.hexdigest()
    finally:
        if digest is not None:
            meta["snapshot_sha256"] = digest


@lru_cache(maxsize=None)
def _schema_validator(name: str) -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parents[1] / "runtime" / "schemas" / name
    try:
        with schema_path.open("r", encoding="utf-8") as file:
            schema = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise analysis_export_error(
            f"Could not load Provider Pressure schema '{schema_path}': {exc}"
        ) from exc
    return Draft202012Validator(schema)


def _validate_schema(name: str, document: Mapping[str, Any]) -> None:
    validator = _schema_validator(name)
    error = min(
        validator.iter_errors(document),
        key=lambda item: list(item.path),
        default=None,
    )
    if error is not None:
        location = ".".join(str(part) for part in error.path) or "<root>"
        raise analysis_export_error(
            f"Provider Pressure schema violation at {location}: {error.message}"
        )


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as exc:
        raise analysis_export_error(f"Could not read {label} '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise analysis_export_error(
            f"{label.capitalize()} '{path}' must contain a YAML mapping."
        )
    return value

def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}



def _list_length(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0

def _number(value: Any) -> float | int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0



def _progress(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)
