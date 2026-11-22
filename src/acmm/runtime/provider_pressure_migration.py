from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import yaml
from yaml.events import (
    AliasEvent,
    DocumentEndEvent,
    DocumentStartEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
    StreamEndEvent,
    StreamStartEvent,
)

from acmm.runtime.provider_pressure_segment_store import (
    OPERATIONAL_PROFILE_MAX_BYTES,
    SEGMENT_POLICY_PRESETS,
    provider_pressure_store_v2_error,
    segmented_provider_pressure_event_writer,
    segmented_provider_pressure_store,
)


LEGACY_PROFILE_SAFE_LOAD_MAX_BYTES = OPERATIONAL_PROFILE_MAX_BYTES
LEGACY_DIGEST_RECOMPUTE_MAX_BYTES = 8 * 1024 * 1024
MIGRATION_DURABILITY_BATCH_RECORDS = 64


class provider_pressure_migration_error(RuntimeError):
    pass


@dataclass(frozen=True)
class migrated_lineage_result:
    legacy_root: Path
    store_root: Path
    snapshot_count: int
    event_count: int
    checkpoint_count: int
    verified_body_digest_count: int
    skipped_body_digest_count: int
    report_path: Path


@dataclass(frozen=True)
class provider_pressure_migration_result:
    source_root: Path
    lineages: tuple[migrated_lineage_result, ...]


class _EventCursor:
    def __init__(self, handle) -> None:
        self._events = iter(yaml.parse(handle, Loader=yaml.SafeLoader))
        self._buffer = None
        self._anchors: dict[str, Any] = {}

    def next(self):
        if self._buffer is not None:
            value = self._buffer
            self._buffer = None
            return value
        return next(self._events)

    def push(self, value) -> None:
        if self._buffer is not None:
            raise provider_pressure_migration_error("YAML cursor pushback overflow.")
        self._buffer = value

    def register_anchor(self, event, value: Any) -> None:
        anchor = getattr(event, "anchor", None)
        if anchor:
            self._anchors[str(anchor)] = value

    def resolve_alias(self, event: AliasEvent) -> Any:
        anchor = str(event.anchor)
        if anchor not in self._anchors:
            raise provider_pressure_migration_error(
                f"Legacy YAML alias '*{anchor}' references an anchor that was not resolved earlier in the document."
            )
        return self._anchors[anchor]


def upgrade_provider_pressure_root(
    source_root: str | Path,
    *,
    progress: Callable[[str], None] | None = None,
    segment_size: str | None = None,
) -> provider_pressure_migration_result:
    if segment_size is not None and segment_size not in SEGMENT_POLICY_PRESETS:
        allowed = ", ".join(sorted(SEGMENT_POLICY_PRESETS))
        raise provider_pressure_migration_error(
            f"Unknown Provider Pressure segment-size preset '{segment_size}'; expected one of: {allowed}."
        )
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise provider_pressure_migration_error(
            f"Provider Pressure root does not exist: '{root}'."
        )
    indexes = sorted(
        path for path in root.rglob("trajectory-index.yaml")
        if "store_v2" not in path.parts
    )
    if not indexes:
        raise provider_pressure_migration_error(
            f"No legacy Provider Pressure trajectory-index.yaml files found under '{root}'."
        )
    _progress(progress, f"Provider Pressure upgrade: discovered {len(indexes)} legacy lineage(s)...")
    results: list[migrated_lineage_result] = []
    for position, index_path in enumerate(indexes, start=1):
        _progress(
            progress,
            f"Provider Pressure upgrade: migrating lineage {position}/{len(indexes)} at '{index_path.parent}'...",
        )
        results.append(_upgrade_lineage(index_path.parent, progress=progress, segment_size=segment_size))
    _progress(progress, "Provider Pressure upgrade: migration and verification complete.")
    return provider_pressure_migration_result(source_root=root, lineages=tuple(results))


def _upgrade_lineage(
    legacy_root: Path,
    *,
    progress: Callable[[str], None] | None,
    segment_size: str | None,
) -> migrated_lineage_result:
    index_path = legacy_root / "trajectory-index.yaml"
    header, entries = _stream_legacy_index(index_path)
    identity = header.get("identity")
    if not isinstance(identity, dict):
        raise provider_pressure_migration_error(
            f"Legacy Provider Pressure index '{index_path}' has no valid identity."
        )
    created_at = datetime.now(timezone.utc)
    store = segmented_provider_pressure_store(
        legacy_root,
        identity,
        segment_policy_preset=segment_size,
    )
    try:
        store.initialize_for_migration(created_at)
    except provider_pressure_store_v2_error as exc:
        raise provider_pressure_migration_error(str(exc)) from exc

    policy = store.segment_policy()
    _progress(
        progress,
        "Provider Pressure upgrade: segment policy "
        f"{policy['preset']} ({_format_bytes(int(policy['max_bytes']))} / "
        f"{int(policy['max_records'])} records); durability batch "
        f"{MIGRATION_DURABILITY_BATCH_RECORDS} events.",
    )

    latest_checkpoint = store.latest_checkpoint()
    last_complete_snapshot = -1
    last_checkpoint_event_sequence = -1
    if latest_checkpoint is not None:
        marker = latest_checkpoint.source_metadata.get("_migration")
        if isinstance(marker, Mapping):
            last_complete_snapshot = int(marker.get("legacy_snapshot_sequence", -1))
        last_checkpoint_event_sequence = latest_checkpoint.event_sequence

    snapshot_count = 0
    verified_digest_count = 0
    skipped_digest_count = 0
    previous_legacy_snapshot_sha: str | None = None
    previous_profile_hash: str | None = None
    if latest_checkpoint is not None:
        previous_profile_hash = _json_digest(latest_checkpoint.profile)

    for entry in entries:
        sequence = int(entry.get("sequence", -1))
        snapshot_count += 1
        claimed_parent = entry.get("parent_sha256")
        if claimed_parent != previous_legacy_snapshot_sha:
            raise provider_pressure_migration_error(
                f"Legacy Provider Pressure index hash chain mismatch at snapshot {sequence}."
            )
        claimed_sha = str(entry.get("snapshot_sha256") or "")
        if not claimed_sha:
            raise provider_pressure_migration_error(
                f"Legacy Provider Pressure index snapshot {sequence} lacks snapshot_sha256."
            )
        relative = entry.get("path")
        if not isinstance(relative, str) or not relative:
            raise provider_pressure_migration_error(
                f"Legacy Provider Pressure index snapshot {sequence} has invalid path."
            )
        snapshot_path = _safe_legacy_artifact(legacy_root, relative)
        if not snapshot_path.is_file():
            raise provider_pressure_migration_error(
                f"Legacy Provider Pressure snapshot does not exist: '{snapshot_path}'."
            )
        if sequence <= last_complete_snapshot:
            previous_legacy_snapshot_sha = claimed_sha
            continue

        if snapshot_path.stat().st_size <= LEGACY_DIGEST_RECOMPUTE_MAX_BYTES:
            _verify_small_legacy_snapshot_digest(snapshot_path, claimed_sha)
            verified_digest_count += 1
        else:
            skipped_digest_count += 1

        existing = store.event_count_after(
            last_checkpoint_event_sequence,
            migration_snapshot_sequence=sequence,
        )
        _progress(
            progress,
            f"Provider Pressure upgrade: streaming legacy snapshot {sequence}"
            + (f" (resume: {sum(existing.values())} event(s) already durable)..." if existing else "..."),
        )
        sealed_for_snapshot = 0
        def _segment_progress(meta: Mapping[str, Any]) -> None:
            nonlocal sealed_for_snapshot
            sealed_for_snapshot += 1
            segment_number = int(meta.get("segment_index", -1))
            segment_bytes = int(meta.get("bytes", 0))
            segment_records = int(meta.get("record_count", 0))
            if sealed_for_snapshot <= 3 or sealed_for_snapshot % 5 == 0:
                total_migrated = int(meta.get("last_event_sequence", -1)) - last_checkpoint_event_sequence
                _progress(
                    progress,
                    "Provider Pressure upgrade: "
                    f"snapshot {sequence}: sealed segment {segment_number} "
                    f"({segment_records} events, {_format_bytes(segment_bytes)}); "
                    f"{total_migrated} event(s) durable since prior checkpoint...",
                )

        try:
            with store.event_writer(
                durability_batch_records=MIGRATION_DURABILITY_BATCH_RECORDS,
                on_segment_sealed=_segment_progress,
            ) as event_writer:
                result = _stream_migrate_snapshot(
                    snapshot_path,
                    event_writer,
                    expected_entry=entry,
                    existing_counts=existing,
                    previous_profile_hash=previous_profile_hash,
                )
        except provider_pressure_store_v2_error as exc:
            raise provider_pressure_migration_error(str(exc)) from exc
        checkpoint_path = store.append_checkpoint(
            profile=result["profile_after"],
            source_kind=str(result["snapshot"]["source_kind"]),
            source_execution_id=str(result["snapshot"]["source_execution_id"]),
            execution_status=str(result["snapshot"].get("execution_status") or result["snapshot"]["source_kind"]),
            source_metadata={
                **dict(result["source_metadata"]),
                "_migration": {
                    "legacy_snapshot_sequence": sequence,
                    "legacy_snapshot_sha256": claimed_sha,
                    "legacy_snapshot_path": snapshot_path.relative_to(legacy_root).as_posix(),
                    "legacy_snapshot_file_sha256": _sha256_file(snapshot_path),
                    "legacy_body_digest_recomputed": snapshot_path.stat().st_size <= LEGACY_DIGEST_RECOMPUTE_MAX_BYTES,
                },
            },
            created_at=_parse_datetime(str(result["snapshot"]["created_at"])),
        )
        latest_checkpoint = store.latest_checkpoint()
        if latest_checkpoint is None or latest_checkpoint.path != checkpoint_path:
            raise provider_pressure_migration_error("Provider Pressure migration checkpoint commit did not become visible.")
        last_complete_snapshot = sequence
        last_checkpoint_event_sequence = latest_checkpoint.event_sequence
        previous_profile_hash = _json_digest(result["profile_after"])
        previous_legacy_snapshot_sha = claimed_sha
        _progress(
            progress,
            f"Provider Pressure upgrade: checkpointed legacy snapshot {sequence}: "
            f"{sum(result['seen'].values())} legacy event(s), "
            f"{sum(result['emitted'].values())} newly migrated event(s).",
        )

    latest_claim = header.get("latest_snapshot_sha256")
    if previous_legacy_snapshot_sha != latest_claim:
        raise provider_pressure_migration_error(
            "Legacy Provider Pressure index latest_snapshot_sha256 does not match streamed terminal entry."
        )

    _migrate_profile_state(legacy_root, store, identity)
    event_count = sum(1 for _ in store.iter_events())
    checkpoint_count = sum(1 for _ in store.iter_checkpoints())
    report = {
        "schema_version": "0.1",
        "source": {
            "legacy_root": str(legacy_root),
            "legacy_index": str(index_path),
            "legacy_latest_snapshot_sha256": latest_claim,
            "legacy_snapshot_count": snapshot_count,
        },
        "destination": {
            "store_root": str(store.root),
            "event_count": event_count,
            "checkpoint_count": checkpoint_count,
            "segment_policy": dict(store.segment_policy()),
            "migration_durability_batch_records": MIGRATION_DURABILITY_BATCH_RECORDS,
        },
        "verification": {
            "index_and_snapshot_metadata_chain_verified": True,
            "small_snapshot_body_digest_verified": verified_digest_count,
            "large_snapshot_body_digest_streamed_without_recanonicalization": skipped_digest_count,
            "event_chain_verified": True,
            "checkpoint_chain_verified": True,
        },
    }
    report_path = store.root / "migration-report.json"
    _write_json_atomically(report_path, report)
    return migrated_lineage_result(
        legacy_root=legacy_root,
        store_root=store.root,
        snapshot_count=snapshot_count,
        event_count=event_count,
        checkpoint_count=checkpoint_count,
        verified_body_digest_count=verified_digest_count,
        skipped_body_digest_count=skipped_digest_count,
        report_path=report_path,
    )


def _stream_legacy_index(path: Path) -> tuple[dict[str, Any], Iterator[dict[str, Any]]]:
    handle = path.open("r", encoding="utf-8")
    cursor = _EventCursor(handle)
    try:
        _expect(cursor, StreamStartEvent)
        _expect(cursor, DocumentStartEvent)
        _expect(cursor, MappingStartEvent)
        header: dict[str, Any] = {}
        snapshots_started = False
        while True:
            event = cursor.next()
            if isinstance(event, MappingEndEvent):
                break
            key = _scalar_value(event)
            if key == "snapshots":
                _expect(cursor, SequenceStartEvent)
                snapshots_started = True
                break
            header[str(key)] = _parse_node(cursor)
        if not snapshots_started:
            handle.close()
            raise provider_pressure_migration_error(f"Legacy Provider Pressure index '{path}' has no snapshots sequence.")
    except BaseException:
        handle.close()
        raise

    def iterator() -> Iterator[dict[str, Any]]:
        try:
            while True:
                event = cursor.next()
                if isinstance(event, SequenceEndEvent):
                    break
                value = _parse_node(cursor, event)
                if not isinstance(value, dict):
                    raise provider_pressure_migration_error(
                        f"Legacy Provider Pressure index '{path}' contains a malformed snapshot entry."
                    )
                yield value
            # Consume any trailing mapping fields conservatively.
            while True:
                event = cursor.next()
                if isinstance(event, MappingEndEvent):
                    break
                _ = _scalar_value(event)
                _skip_node(cursor)
            _expect(cursor, DocumentEndEvent)
            _expect(cursor, StreamEndEvent)
        finally:
            handle.close()
    return header, iterator()


def _stream_migrate_snapshot(
    path: Path,
    event_writer: segmented_provider_pressure_event_writer,
    *,
    expected_entry: Mapping[str, Any],
    existing_counts: Mapping[str, int],
    previous_profile_hash: str | None,
) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        cursor = _EventCursor(handle)
        _expect(cursor, StreamStartEvent)
        _expect(cursor, DocumentStartEvent)
        _expect(cursor, MappingStartEvent)
        snapshot_meta: dict[str, Any] | None = None
        source_metadata: dict[str, Any] = {}
        profile_after: dict[str, Any] | None = None
        emitted = {"signal": 0, "decision": 0, "pacing": 0}
        seen = {"signal": 0, "decision": 0, "pacing": 0}
        while True:
            event = cursor.next()
            if isinstance(event, MappingEndEvent):
                break
            key = str(_scalar_value(event))
            if key == "schema_version":
                _ = _parse_node(cursor)
            elif key == "snapshot":
                value = _parse_node(cursor)
                if not isinstance(value, dict):
                    raise provider_pressure_migration_error(f"Legacy snapshot '{path}' has malformed snapshot metadata.")
                snapshot_meta = value
                if int(value.get("sequence", -1)) != int(expected_entry.get("sequence", -2)):
                    raise provider_pressure_migration_error(f"Legacy snapshot sequence mismatch for '{path}'.")
                if value.get("snapshot_sha256") != expected_entry.get("snapshot_sha256"):
                    raise provider_pressure_migration_error(f"Legacy snapshot digest metadata mismatch for '{path}'.")
                if value.get("parent_sha256") != expected_entry.get("parent_sha256"):
                    raise provider_pressure_migration_error(f"Legacy snapshot parent metadata mismatch for '{path}'.")
            elif key == "identity":
                _skip_node(cursor)
            elif key == "source_metadata":
                source_metadata = _parse_source_metadata(cursor)
            elif key == "profile_before":
                before = _parse_node(cursor)
                if not isinstance(before, dict):
                    raise provider_pressure_migration_error(f"Legacy snapshot '{path}' has malformed profile_before.")
                if previous_profile_hash is not None and _json_digest(before) != previous_profile_hash:
                    raise provider_pressure_migration_error(
                        f"Legacy snapshot '{path}' profile_before does not match the preceding migrated checkpoint."
                    )
            elif key == "learning_delta":
                if snapshot_meta is None:
                    raise provider_pressure_migration_error(f"Legacy snapshot '{path}' learning_delta precedes snapshot metadata.")
                _migrate_learning_delta(
                    cursor,
                    event_writer,
                    snapshot_meta=snapshot_meta,
                    existing_counts=existing_counts,
                    emitted=emitted,
                    seen=seen,
                )
            elif key == "profile_after":
                value = _parse_node(cursor)
                if not isinstance(value, dict):
                    raise provider_pressure_migration_error(f"Legacy snapshot '{path}' has malformed profile_after.")
                profile_after = value
            else:
                _skip_node(cursor)
        _expect(cursor, DocumentEndEvent)
        _expect(cursor, StreamEndEvent)
    if snapshot_meta is None or profile_after is None:
        raise provider_pressure_migration_error(f"Legacy snapshot '{path}' is incomplete.")
    for kind, count in existing_counts.items():
        if kind in seen and int(count) > seen[kind]:
            raise provider_pressure_migration_error(
                f"Migrated event resume state exceeds legacy {kind} count in '{path}'."
            )
    return {
        "snapshot": snapshot_meta,
        "source_metadata": source_metadata,
        "profile_after": profile_after,
        "seen": seen,
        "emitted": emitted,
    }


def _migrate_learning_delta(
    cursor: _EventCursor,
    event_writer: segmented_provider_pressure_event_writer,
    *,
    snapshot_meta: Mapping[str, Any],
    existing_counts: Mapping[str, int],
    emitted: dict[str, int],
    seen: dict[str, int],
) -> None:
    _expect(cursor, MappingStartEvent)
    mapping = {"signals": "signal", "decisions": "decision", "pacing_events": "pacing"}
    while True:
        event = cursor.next()
        if isinstance(event, MappingEndEvent):
            break
        key = str(_scalar_value(event))
        kind = mapping.get(key)
        if kind is None:
            _skip_node(cursor)
            continue
        _expect(cursor, SequenceStartEvent)
        while True:
            item_event = cursor.next()
            if isinstance(item_event, SequenceEndEvent):
                break
            value = _parse_node(cursor, item_event)
            if not isinstance(value, dict):
                raise provider_pressure_migration_error("Legacy Provider Pressure learning_delta contains a non-object record.")
            seen[kind] += 1
            if seen[kind] <= int(existing_counts.get(kind, 0)):
                continue
            marker = {
                "legacy_snapshot_sequence": int(snapshot_meta["sequence"]),
                "legacy_kind_index": seen[kind],
            }
            payload = dict(value)
            payload["_migration"] = marker
            event_writer.append_event(
                kind=kind,
                execution_id=str(snapshot_meta.get("source_execution_id") or ""),
                payload=payload,
                created_at=_parse_datetime(str(snapshot_meta["created_at"])),
            )
            emitted[kind] += 1


def _parse_source_metadata(cursor: _EventCursor) -> dict[str, Any]:
    _expect(cursor, MappingStartEvent)
    result: dict[str, Any] = {}
    while True:
        event = cursor.next()
        if isinstance(event, MappingEndEvent):
            break
        key = str(_scalar_value(event))
        if key == "pacing_events":
            _skip_node(cursor)
        else:
            result[key] = _parse_node(cursor)
    return result


def _migrate_profile_state(
    legacy_root: Path,
    store: segmented_provider_pressure_store,
    identity: Mapping[str, Any],
) -> None:
    source = legacy_root / "profile-state.yaml"
    if not source.is_file():
        latest = store.latest_checkpoint()
        if latest is None:
            return
        profile = {
            "schema_version": "0.6",
            "identity": dict(identity),
            "sequence": int(latest.profile.get("sequence", 0)),
            "created_at": latest.created_at,
            "updated_at": latest.profile.get("updated_at", latest.created_at),
            "features": dict(latest.profile.get("features", {})),
            "model_state": dict(latest.profile.get("model_state", {})),
            "totals": dict(latest.profile.get("totals", {})),
        }
        store.persist_profile(profile)
        return
    size = source.stat().st_size
    if size > LEGACY_PROFILE_SAFE_LOAD_MAX_BYTES:
        raise provider_pressure_migration_error(
            f"Legacy Provider Pressure operational profile is {size} bytes, exceeding the safe migration bound. "
            "The current state itself requires model-specific compaction before it can be resumed safely."
        )
    try:
        profile = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise provider_pressure_migration_error(f"Could not read legacy Provider Pressure profile '{source}': {exc}") from exc
    if not isinstance(profile, dict):
        raise provider_pressure_migration_error(f"Legacy Provider Pressure profile '{source}' is malformed.")
    for source_key in profile.get("applied_sources", ()):
        if isinstance(source_key, str) and source_key:
            store.mark_source_applied(source_key, created_at=datetime.now(timezone.utc))
            if ":" in source_key:
                store.mark_source_applied(
                    "execution:" + source_key.split(":", 1)[1],
                    created_at=datetime.now(timezone.utc),
                )
    profile = dict(profile)
    profile["schema_version"] = "0.6"
    profile.pop("applied_sources", None)
    profile.setdefault("model_state", {})
    store.persist_profile(profile)


def _verify_small_legacy_snapshot_digest(path: Path, expected: str) -> None:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise provider_pressure_migration_error(f"Could not read legacy Provider Pressure snapshot '{path}': {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("snapshot"), dict):
        raise provider_pressure_migration_error(f"Legacy Provider Pressure snapshot '{path}' is malformed.")
    observed = value["snapshot"].pop("snapshot_sha256", None)
    payload = yaml.safe_dump(value, sort_keys=True, allow_unicode=False).encode("utf-8")
    actual = hashlib.sha256(payload).hexdigest()
    if observed != expected or actual != expected:
        raise provider_pressure_migration_error(f"Legacy Provider Pressure snapshot digest mismatch for '{path}'.")


def _parse_node(cursor: _EventCursor, first=None):
    event = first if first is not None else cursor.next()
    if isinstance(event, AliasEvent):
        return cursor.resolve_alias(event)
    if isinstance(event, ScalarEvent):
        value = _scalar_value(event)
        cursor.register_anchor(event, value)
        return value
    if isinstance(event, MappingStartEvent):
        result: dict[str, Any] = {}
        cursor.register_anchor(event, result)
        while True:
            child = cursor.next()
            if isinstance(child, MappingEndEvent):
                break
            key_value = _parse_node(cursor, child)
            if isinstance(key_value, (dict, list)):
                raise provider_pressure_migration_error(
                    "Legacy YAML mapping contains a non-scalar key, which is unsupported by the Provider Pressure upgrader."
                )
            result[str(key_value)] = _parse_node(cursor)
        return result
    if isinstance(event, SequenceStartEvent):
        result: list[Any] = []
        cursor.register_anchor(event, result)
        while True:
            child = cursor.next()
            if isinstance(child, SequenceEndEvent):
                break
            result.append(_parse_node(cursor, child))
        return result
    raise provider_pressure_migration_error(f"Unexpected YAML event {type(event).__name__}.")


def _skip_node(cursor: _EventCursor, first=None) -> None:
    event = first if first is not None else cursor.next()
    if isinstance(event, AliasEvent):
        cursor.resolve_alias(event)
        return
    if isinstance(event, ScalarEvent):
        value = _scalar_value(event)
        cursor.register_anchor(event, value)
        return
    if isinstance(event, MappingStartEvent):
        if event.anchor:
            _parse_node(cursor, event)
            return
        while True:
            child = cursor.next()
            if isinstance(child, MappingEndEvent):
                break
            _skip_node(cursor, child)
            _skip_node(cursor)
        return
    if isinstance(event, SequenceStartEvent):
        if event.anchor:
            _parse_node(cursor, event)
            return
        while True:
            child = cursor.next()
            if isinstance(child, SequenceEndEvent):
                break
            _skip_node(cursor, child)
        return
    raise provider_pressure_migration_error(f"Unexpected YAML event {type(event).__name__} while skipping node.")


def _expect(cursor: _EventCursor, event_type):
    event = cursor.next()
    if not isinstance(event, event_type):
        raise provider_pressure_migration_error(
            f"Expected YAML {event_type.__name__}, observed {type(event).__name__}."
        )
    return event


def _scalar_value(event) -> Any:
    if not isinstance(event, ScalarEvent):
        raise provider_pressure_migration_error(
            f"Expected YAML scalar, observed {type(event).__name__}."
        )
    if event.style is not None or event.tag in {
        "tag:yaml.org,2002:str",
        "tag:yaml.org,2002:timestamp",
    }:
        return event.value
    try:
        value = yaml.safe_load(event.value)
    except yaml.YAMLError:
        return event.value
    if not isinstance(value, (str, int, float, bool, type(None))):
        return event.value
    return value


def _safe_legacy_artifact(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise provider_pressure_migration_error(f"Unsafe legacy Provider Pressure path '{relative}'.")
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise provider_pressure_migration_error(f"Unsafe legacy Provider Pressure path '{relative}'.") from exc
    if candidate.is_symlink():
        raise provider_pressure_migration_error(f"Refusing symbolic-link legacy Provider Pressure artifact '{candidate}'.")
    return candidate


def _parse_datetime(value: str) -> datetime:
    rendered = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(rendered)
    except ValueError as exc:
        raise provider_pressure_migration_error(f"Invalid Provider Pressure timestamp '{value}'.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _json_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(value), sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomically(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoded = (json.dumps(dict(value), ensure_ascii=True, indent=2) + "\n").encode("ascii")
    try:
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)



def _format_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    if size < 1024:
        return f"{int(size)} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"

def _progress(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)
