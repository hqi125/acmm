from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable, Iterator, Mapping

import yaml

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


STORE_SCHEMA_VERSION = "0.2"
EVENT_SCHEMA_VERSION = "0.1"
CHECKPOINT_SCHEMA_VERSION = "0.1"
SEGMENT_META_SCHEMA_VERSION = "0.1"
STORE_DIR_NAME = "store_v2"
SEGMENT_POLICY_PRESETS: dict[str, tuple[int, int]] = {
    "small": (1024 * 1024, 256),
    "balanced": (8 * 1024 * 1024, 2048),
    "large": (16 * 1024 * 1024, 4096),
    "xlarge": (32 * 1024 * 1024, 8192),
}
DEFAULT_SEGMENT_POLICY_PRESET = "balanced"
LEGACY_SEGMENT_POLICY_PRESET = "legacy-small"
LEGACY_SEGMENT_MAX_BYTES = 1024 * 1024
LEGACY_SEGMENT_MAX_RECORDS = 256
SEGMENT_MAX_BYTES, SEGMENT_MAX_RECORDS = SEGMENT_POLICY_PRESETS[DEFAULT_SEGMENT_POLICY_PRESET]
INLINE_PAYLOAD_MAX_BYTES = 128 * 1024
OPERATIONAL_PROFILE_MAX_BYTES = 16 * 1024 * 1024

_LOCAL_LOCKS_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.RLock] = {}


class provider_pressure_store_v2_error(RuntimeError):
    pass


class provider_pressure_store_upgrade_required(provider_pressure_store_v2_error):
    pass


@dataclass(frozen=True)
class pressure_event:
    sequence: int
    created_at: str
    kind: str
    execution_id: str
    event_sha256: str
    parent_sha256: str | None
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class pressure_checkpoint:
    sequence: int
    event_sequence: int
    created_at: str
    source_kind: str
    source_execution_id: str
    execution_status: str
    checkpoint_sha256: str
    parent_checkpoint_sha256: str | None
    profile: Mapping[str, Any]
    source_metadata: Mapping[str, Any]
    path: Path


class segmented_provider_pressure_store:
    def __init__(
        self,
        legacy_root: Path,
        identity: Mapping[str, str],
        *,
        segment_policy_preset: str | None = None,
    ) -> None:
        self.legacy_root = Path(legacy_root)
        self.root = self.legacy_root / STORE_DIR_NAME
        self.identity = dict(identity)
        self.requested_segment_policy_preset = segment_policy_preset
        self.profile_path = self.root / "profile-state.yaml"
        self.lineage_path = self.root / "lineage.json"
        self.segments_dir = self.root / "segments"
        self.checkpoints_dir = self.root / "checkpoints"
        self.blobs_dir = self.root / "blobs"
        self.applied_sources_dir = self.root / "applied_sources"
        self.lock_path = self.root / ".store.lock"
        lock_key = str(self.lock_path)
        with _LOCAL_LOCKS_GUARD:
            self._local_lock = _LOCAL_LOCKS.setdefault(lock_key, threading.RLock())

    @property
    def legacy_index_path(self) -> Path:
        return self.legacy_root / "trajectory-index.yaml"

    def requires_upgrade(self) -> bool:
        return self.legacy_index_path.is_file() and not self.lineage_path.is_file()

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._local_lock:
            self.root.mkdir(parents=True, exist_ok=True)
            if fcntl is None:
                yield
                return
            with self.lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def ensure_ready(self, created_at: datetime) -> None:
        with self.locked():
            if self.lineage_path.is_file():
                lineage = self._load_lineage_unlocked()
                self._ensure_segment_policy_unlocked(lineage)
                self._recover_active_segment_unlocked(lineage)
                return
            if self.legacy_index_path.is_file():
                raise provider_pressure_store_upgrade_required(
                    "Legacy Provider Pressure trajectory detected. Run "
                    "'python tools/upgrade_provider_pressure_store.py <provider_pressure-root>' "
                    "before starting a new execution so history is not forked."
                )
            self._initialize_unlocked(created_at)

    def initialize_for_migration(self, created_at: datetime) -> None:
        with self.locked():
            if not self.lineage_path.is_file():
                self._initialize_unlocked(created_at)
            else:
                lineage = self._load_lineage_unlocked()
                self._ensure_segment_policy_unlocked(lineage)
                self._recover_active_segment_unlocked(lineage)

    def _initialize_unlocked(self, created_at: datetime) -> None:
        self.segments_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.applied_sources_dir.mkdir(parents=True, exist_ok=True)
        policy = _segment_policy_record(self.requested_segment_policy_preset or DEFAULT_SEGMENT_POLICY_PRESET)
        lineage = {
            "schema_version": STORE_SCHEMA_VERSION,
            "identity": dict(self.identity),
            "created_at": _iso(created_at),
            "updated_at": _iso(created_at),
            "latest_event_sequence": -1,
            "latest_event_sha256": None,
            "active_segment_index": 0,
            "active_segment_record_count": 0,
            "active_segment_bytes": 0,
            "latest_sealed_segment_sha256": None,
            "latest_checkpoint_sequence": -1,
            "latest_checkpoint": None,
            "latest_checkpoint_sha256": None,
            "segment_policy": policy,
        }
        _write_json_atomically(self.lineage_path, lineage)

    def segment_policy(self) -> Mapping[str, Any]:
        with self.locked():
            lineage = self._load_lineage_unlocked()
            self._ensure_segment_policy_unlocked(lineage)
            lineage = self._load_lineage_unlocked()
            return dict(self._segment_policy_unlocked(lineage))

    def load_profile(self) -> dict[str, Any] | None:
        with self.locked():
            if not self.profile_path.is_file():
                return None
            try:
                size = self.profile_path.stat().st_size
                if size > OPERATIONAL_PROFILE_MAX_BYTES:
                    raise provider_pressure_store_v2_error(
                        f"Provider Pressure operational profile '{self.profile_path}' is {size} bytes; "
                        "provider model state must be compacted before execution can continue."
                    )
                with self.profile_path.open("r", encoding="utf-8") as handle:
                    value = yaml.safe_load(handle)
            except provider_pressure_store_v2_error:
                raise
            except (OSError, yaml.YAMLError) as exc:
                raise provider_pressure_store_v2_error(
                    f"Could not read Provider Pressure v2 profile '{self.profile_path}': {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise provider_pressure_store_v2_error(
                    f"Provider Pressure v2 profile '{self.profile_path}' is malformed."
                )
            return value

    def persist_profile(self, profile: Mapping[str, Any]) -> None:
        with self.locked():
            _write_yaml_atomically(self.profile_path, profile)

    def source_applied(self, source_key: str) -> bool:
        marker = self._source_marker_path(source_key)
        return marker.is_file()

    def mark_source_applied(self, source_key: str, *, created_at: datetime) -> None:
        with self.locked():
            marker = self._source_marker_path(source_key)
            if marker.is_file():
                return
            marker.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomically(
                marker,
                {
                    "schema_version": "0.1",
                    "source_key": source_key,
                    "created_at": _iso(created_at),
                },
            )

    def iter_applied_sources(self) -> Iterator[str]:
        if not self.applied_sources_dir.is_dir():
            return
        for marker in sorted(self.applied_sources_dir.glob("*.json")):
            try:
                value = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            source_key = value.get("source_key") if isinstance(value, dict) else None
            if isinstance(source_key, str) and source_key:
                yield source_key

    def append_event(
        self,
        *,
        kind: str,
        execution_id: str,
        payload: Mapping[str, Any],
        created_at: datetime,
    ) -> int:
        with self.event_writer(durability_batch_records=1) as writer:
            return writer.append_event(
                kind=kind,
                execution_id=execution_id,
                payload=payload,
                created_at=created_at,
            )

    def event_writer(
        self,
        *,
        durability_batch_records: int = 1,
        on_segment_sealed: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> "segmented_provider_pressure_event_writer":
        return segmented_provider_pressure_event_writer(
            self,
            durability_batch_records=durability_batch_records,
            on_segment_sealed=on_segment_sealed,
        )

    def append_checkpoint(
        self,
        *,
        profile: Mapping[str, Any],
        source_kind: str,
        source_execution_id: str,
        execution_status: str,
        source_metadata: Mapping[str, Any] | None,
        created_at: datetime,
    ) -> Path:
        with self.locked():
            lineage = self._load_lineage_unlocked()
            self._recover_active_segment_unlocked(lineage)
            lineage = self._load_lineage_unlocked()
            sequence = int(lineage.get("latest_checkpoint_sequence", -1)) + 1
            checkpoint_path = self.checkpoints_dir / f"checkpoint_{sequence:06d}.json"
            profile_record = self._checkpoint_profile_record_unlocked(profile)
            checkpoint_meta = {
                "sequence": sequence,
                "event_sequence": int(lineage.get("latest_event_sequence", -1)),
                "event_sha256": lineage.get("latest_event_sha256"),
                "created_at": _iso(created_at),
                "source_kind": str(source_kind),
                "source_execution_id": str(source_execution_id),
                "execution_status": str(execution_status),
                "parent_checkpoint_sha256": lineage.get("latest_checkpoint_sha256"),
            }
            body: dict[str, Any] = {
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "checkpoint": checkpoint_meta,
                "source_metadata": dict(source_metadata or {}),
                **profile_record,
            }
            digest = _json_digest(body)
            checkpoint_meta["checkpoint_sha256"] = digest
            _write_json_atomically(checkpoint_path, body)
            lineage["latest_checkpoint_sequence"] = sequence
            lineage["latest_checkpoint"] = checkpoint_path.relative_to(self.root).as_posix()
            lineage["latest_checkpoint_sha256"] = digest
            lineage["updated_at"] = _iso(created_at)
            _write_json_atomically(self.lineage_path, lineage)
            return checkpoint_path

    def latest_checkpoint(self) -> pressure_checkpoint | None:
        with self.locked():
            lineage = self._load_lineage_unlocked()
            relative = lineage.get("latest_checkpoint")
            if not isinstance(relative, str) or not relative:
                return None
            return self._load_checkpoint_unlocked(self.root / relative)

    def iter_checkpoints(self) -> Iterator[pressure_checkpoint]:
        if not self.checkpoints_dir.is_dir():
            return
        expected_parent: str | None = None
        expected_sequence = 0
        for path in sorted(self.checkpoints_dir.glob("checkpoint_*.json")):
            checkpoint = self._load_checkpoint_unlocked(path)
            if checkpoint.sequence != expected_sequence:
                raise provider_pressure_store_v2_error(
                    f"Provider Pressure checkpoint sequence is discontinuous at {expected_sequence}."
                )
            if checkpoint.parent_checkpoint_sha256 != expected_parent:
                raise provider_pressure_store_v2_error(
                    f"Provider Pressure checkpoint hash chain mismatch at {path}."
                )
            expected_sequence += 1
            expected_parent = checkpoint.checkpoint_sha256
            yield checkpoint

    def iter_events(self) -> Iterator[pressure_event]:
        expected_sequence = 0
        expected_parent: str | None = None
        previous_segment_sha256: str | None = None
        for segment_path in sorted(self.segments_dir.glob("segment_*.jsonl")):
            segment_index = _segment_index(segment_path)
            meta_path = self._segment_meta_path(segment_index)
            sealed = meta_path.is_file()
            if sealed:
                meta = _load_json_object(meta_path)
                if meta.get("schema_version") != SEGMENT_META_SCHEMA_VERSION:
                    raise provider_pressure_store_v2_error(
                        f"Unsupported Provider Pressure segment metadata schema in '{meta_path}'."
                    )
                if meta.get("parent_segment_sha256") != previous_segment_sha256:
                    raise provider_pressure_store_v2_error(
                        f"Provider Pressure segment chain mismatch at '{segment_path}'."
                    )
                actual_segment_sha = _sha256_file(segment_path)
                if meta.get("segment_sha256") != actual_segment_sha:
                    raise provider_pressure_store_v2_error(
                        f"Provider Pressure sealed segment digest mismatch at '{segment_path}'."
                    )
                previous_segment_sha256 = actual_segment_sha
            with segment_path.open("rb") as handle:
                for raw in handle:
                    if not raw.endswith(b"\n"):
                        if sealed:
                            raise provider_pressure_store_v2_error(
                                f"Provider Pressure sealed segment '{segment_path}' has a truncated tail."
                            )
                        break
                    try:
                        record = json.loads(raw.decode("ascii"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise provider_pressure_store_v2_error(
                            f"Provider Pressure segment '{segment_path}' contains malformed JSON."
                        ) from exc
                    event = self._event_from_record(record)
                    if event.sequence != expected_sequence:
                        raise provider_pressure_store_v2_error(
                            f"Provider Pressure event sequence is discontinuous at {expected_sequence}."
                        )
                    if event.parent_sha256 != expected_parent:
                        raise provider_pressure_store_v2_error(
                            f"Provider Pressure event hash chain mismatch at sequence {event.sequence}."
                        )
                    expected_sequence += 1
                    expected_parent = event.event_sha256
                    yield event

    def event_count_after(self, sequence: int, *, migration_snapshot_sequence: int | None = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.iter_events():
            if event.sequence <= sequence:
                continue
            if migration_snapshot_sequence is not None:
                marker = event.payload.get("_migration")
                if not isinstance(marker, Mapping) or marker.get("legacy_snapshot_sequence") != migration_snapshot_sequence:
                    continue
            counts[event.kind] = counts.get(event.kind, 0) + 1
        return counts

    def _load_checkpoint_unlocked(self, path: Path) -> pressure_checkpoint:
        value = _load_json_object(path)
        if value.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise provider_pressure_store_v2_error(
                f"Unsupported Provider Pressure checkpoint schema in '{path}'."
            )
        checkpoint = value.get("checkpoint")
        if not isinstance(checkpoint, dict):
            raise provider_pressure_store_v2_error(f"Malformed Provider Pressure checkpoint '{path}'.")
        observed = str(checkpoint.get("checkpoint_sha256") or "")
        body = json.loads(json.dumps(value))
        body["checkpoint"].pop("checkpoint_sha256", None)
        expected = _json_digest(body)
        if observed != expected:
            raise provider_pressure_store_v2_error(
                f"Provider Pressure checkpoint digest mismatch for '{path}'."
            )
        profile = value.get("profile")
        profile_ref = value.get("profile_ref")
        if isinstance(profile, dict):
            resolved_profile = profile
        elif isinstance(profile_ref, dict):
            resolved_profile = self._load_blob_unlocked(profile_ref)
        else:
            raise provider_pressure_store_v2_error(f"Provider Pressure checkpoint '{path}' has no profile payload.")
        source_metadata = value.get("source_metadata")
        if not isinstance(source_metadata, dict):
            source_metadata = {}
        return pressure_checkpoint(
            sequence=int(checkpoint["sequence"]),
            event_sequence=int(checkpoint["event_sequence"]),
            created_at=str(checkpoint["created_at"]),
            source_kind=str(checkpoint["source_kind"]),
            source_execution_id=str(checkpoint["source_execution_id"]),
            execution_status=str(checkpoint["execution_status"]),
            checkpoint_sha256=observed,
            parent_checkpoint_sha256=checkpoint.get("parent_checkpoint_sha256"),
            profile=resolved_profile,
            source_metadata=source_metadata,
            path=path,
        )

    def _event_from_record(self, record: Mapping[str, Any]) -> pressure_event:
        if record.get("schema_version") != EVENT_SCHEMA_VERSION:
            raise provider_pressure_store_v2_error("Unsupported Provider Pressure event schema.")
        meta = record.get("event")
        if not isinstance(meta, dict):
            raise provider_pressure_store_v2_error("Malformed Provider Pressure event metadata.")
        observed = str(meta.get("event_sha256") or "")
        body = json.loads(json.dumps(record))
        body["event"].pop("event_sha256", None)
        expected = _json_digest(body)
        if observed != expected:
            raise provider_pressure_store_v2_error(
                f"Provider Pressure event digest mismatch at sequence {meta.get('sequence')}."
            )
        payload = record.get("payload")
        payload_ref = record.get("payload_ref")
        if isinstance(payload, dict):
            resolved = payload
        elif isinstance(payload_ref, dict):
            resolved = self._load_blob_unlocked(payload_ref)
        else:
            resolved = {}
        return pressure_event(
            sequence=int(meta["sequence"]),
            created_at=str(meta["created_at"]),
            kind=str(meta["kind"]),
            execution_id=str(record.get("execution_id") or ""),
            event_sha256=observed,
            parent_sha256=meta.get("parent_sha256"),
            payload=resolved,
        )

    def _payload_record_unlocked(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(dict(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        if len(encoded) <= INLINE_PAYLOAD_MAX_BYTES:
            return {"payload": dict(payload)}
        ref = self._write_blob_unlocked(encoded)
        return {"payload_ref": ref}

    def _checkpoint_profile_record_unlocked(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(dict(profile), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        if len(encoded) <= INLINE_PAYLOAD_MAX_BYTES:
            return {"profile": dict(profile)}
        ref = self._write_blob_unlocked(encoded)
        return {"profile_ref": ref}

    def _write_blob_unlocked(self, encoded: bytes) -> dict[str, Any]:
        digest = hashlib.sha256(encoded).hexdigest()
        path = self.blobs_dir / f"{digest}.json"
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".json.tmp")
            try:
                with temporary.open("wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return {
            "path": path.relative_to(self.root).as_posix(),
            "sha256": digest,
            "bytes": len(encoded),
        }

    def _load_blob_unlocked(self, ref: Mapping[str, Any]) -> dict[str, Any]:
        relative = ref.get("path")
        expected = ref.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise provider_pressure_store_v2_error("Malformed Provider Pressure blob reference.")
        path = (self.root / relative).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise provider_pressure_store_v2_error("Unsafe Provider Pressure blob path.") from exc
        actual = _sha256_file(path)
        if actual != expected:
            raise provider_pressure_store_v2_error(f"Provider Pressure blob digest mismatch for '{path}'.")
        value = _load_json_object(path)
        return value

    def _source_marker_path(self, source_key: str) -> Path:
        digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()
        return self.applied_sources_dir / f"{digest}.json"

    def _segment_path(self, index: int) -> Path:
        return self.segments_dir / f"segment_{index:06d}.jsonl"

    def _segment_meta_path(self, index: int) -> Path:
        return self.segments_dir / f"segment_{index:06d}.meta.json"

    def _rotate_if_needed_unlocked(
        self,
        lineage: dict[str, Any],
        *,
        incoming_bytes: int = 0,
    ) -> None:
        count = int(lineage.get("active_segment_record_count", 0))
        size = int(lineage.get("active_segment_bytes", 0))
        if count <= 0:
            return
        policy = self._segment_policy_unlocked(lineage)
        if count < int(policy["max_records"]) and size + incoming_bytes <= int(policy["max_bytes"]):
            return
        self._seal_active_segment_unlocked(lineage)

    def _seal_active_segment_unlocked(self, lineage: dict[str, Any]) -> None:
        index = int(lineage["active_segment_index"])
        path = self._segment_path(index)
        count = int(lineage.get("active_segment_record_count", 0))
        if count <= 0 or not path.is_file():
            return
        segment_sha = _sha256_file(path)
        first_sequence = int(lineage["latest_event_sequence"]) - count + 1
        meta = {
            "schema_version": SEGMENT_META_SCHEMA_VERSION,
            "segment_index": index,
            "path": path.relative_to(self.root).as_posix(),
            "first_event_sequence": first_sequence,
            "last_event_sequence": int(lineage["latest_event_sequence"]),
            "last_event_sha256": lineage.get("latest_event_sha256"),
            "record_count": count,
            "bytes": path.stat().st_size,
            "segment_sha256": segment_sha,
            "parent_segment_sha256": lineage.get("latest_sealed_segment_sha256"),
        }
        _write_json_atomically(self._segment_meta_path(index), meta)
        lineage["latest_sealed_segment_sha256"] = segment_sha
        lineage["active_segment_index"] = index + 1
        lineage["active_segment_record_count"] = 0
        lineage["active_segment_bytes"] = 0
        _write_json_atomically(self.lineage_path, lineage)

    def _recover_active_segment_unlocked(self, lineage: dict[str, Any]) -> None:
        index = int(lineage.get("active_segment_index", 0))
        path = self._segment_path(index)
        if not path.is_file():
            return
        previous_event_sha = self._previous_segment_last_event_sha(index)
        expected_sequence = self._previous_segment_last_sequence(index) + 1
        count = 0
        valid_bytes = 0
        latest_sha = previous_event_sha
        latest_sequence = expected_sequence - 1
        with path.open("rb+") as handle:
            while True:
                start = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    handle.truncate(start)
                    handle.flush()
                    os.fsync(handle.fileno())
                    break
                try:
                    record = json.loads(raw.decode("ascii"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise provider_pressure_store_v2_error(
                        f"Provider Pressure active segment '{path}' is corrupted before its durable tail."
                    ) from exc
                event = self._event_from_record(record)
                if event.sequence != expected_sequence or event.parent_sha256 != latest_sha:
                    raise provider_pressure_store_v2_error(
                        f"Provider Pressure active segment '{path}' has a discontinuous event chain."
                    )
                expected_sequence += 1
                count += 1
                valid_bytes += len(raw)
                latest_sha = event.event_sha256
                latest_sequence = event.sequence
        changed = (
            int(lineage.get("active_segment_record_count", -1)) != count
            or int(lineage.get("active_segment_bytes", -1)) != valid_bytes
            or int(lineage.get("latest_event_sequence", -1)) != latest_sequence
            or lineage.get("latest_event_sha256") != latest_sha
        )
        if changed:
            lineage["active_segment_record_count"] = count
            lineage["active_segment_bytes"] = valid_bytes
            lineage["latest_event_sequence"] = latest_sequence
            lineage["latest_event_sha256"] = latest_sha
            _write_json_atomically(self.lineage_path, lineage)

    def _previous_segment_last_sequence(self, active_index: int) -> int:
        if active_index <= 0:
            return -1
        meta = _load_json_object(self._segment_meta_path(active_index - 1))
        return int(meta["last_event_sequence"])

    def _previous_segment_last_event_sha(self, active_index: int) -> str | None:
        if active_index <= 0:
            return None
        meta = _load_json_object(self._segment_meta_path(active_index - 1))
        value = meta.get("last_event_sha256")
        return str(value) if value else None

    def _segment_policy_unlocked(self, lineage: Mapping[str, Any]) -> dict[str, Any]:
        value = lineage.get("segment_policy")
        if isinstance(value, Mapping):
            try:
                preset = str(value["preset"])
                max_bytes = int(value["max_bytes"])
                max_records = int(value["max_records"])
            except (KeyError, TypeError, ValueError) as exc:
                raise provider_pressure_store_v2_error("Malformed Provider Pressure segment policy.") from exc
            if max_bytes <= 0 or max_records <= 0:
                raise provider_pressure_store_v2_error("Provider Pressure segment policy bounds must be positive.")
            return {"preset": preset, "max_bytes": max_bytes, "max_records": max_records}
        return _segment_policy_record(LEGACY_SEGMENT_POLICY_PRESET)

    def _ensure_segment_policy_unlocked(self, lineage: dict[str, Any]) -> None:
        existing = lineage.get("segment_policy")
        requested = self.requested_segment_policy_preset
        if isinstance(existing, Mapping):
            current = self._segment_policy_unlocked(lineage)
            if requested is not None and current != _segment_policy_record(requested):
                raise provider_pressure_store_v2_error(
                    f"Provider Pressure store already uses segment policy '{current['preset']}', "
                    f"not requested policy '{requested}'."
                )
            return
        has_events = int(lineage.get("latest_event_sequence", -1)) >= 0
        if requested is not None and not has_events:
            policy = _segment_policy_record(requested)
        elif requested is not None and has_events and requested != LEGACY_SEGMENT_POLICY_PRESET:
            raise provider_pressure_store_v2_error(
                "Provider Pressure store predates persisted segment-policy metadata and already contains events; "
                "resume it without --segment-size so the legacy 1 MiB / 256-record policy is preserved."
            )
        else:
            policy = _segment_policy_record(LEGACY_SEGMENT_POLICY_PRESET)
        lineage["segment_policy"] = policy
        _write_json_atomically(self.lineage_path, lineage)

    def _load_lineage_unlocked(self) -> dict[str, Any]:
        if not self.lineage_path.is_file():
            raise provider_pressure_store_v2_error(
                f"Provider Pressure v2 lineage does not exist: '{self.lineage_path}'."
            )
        value = _load_json_object(self.lineage_path)
        if value.get("schema_version") != STORE_SCHEMA_VERSION:
            raise provider_pressure_store_v2_error(
                f"Unsupported Provider Pressure store schema_version '{value.get('schema_version')}'."
            )
        if value.get("identity") != self.identity:
            raise provider_pressure_store_v2_error("Provider Pressure v2 lineage identity mismatch.")
        return value



def _segment_policy_record(preset: str) -> dict[str, Any]:
    if preset == LEGACY_SEGMENT_POLICY_PRESET:
        return {
            "preset": LEGACY_SEGMENT_POLICY_PRESET,
            "max_bytes": LEGACY_SEGMENT_MAX_BYTES,
            "max_records": LEGACY_SEGMENT_MAX_RECORDS,
        }
    try:
        max_bytes, max_records = SEGMENT_POLICY_PRESETS[preset]
    except KeyError as exc:
        allowed = ", ".join(sorted(SEGMENT_POLICY_PRESETS))
        raise provider_pressure_store_v2_error(
            f"Unknown Provider Pressure segment policy '{preset}'; expected one of: {allowed}."
        ) from exc
    return {
        "preset": preset,
        "max_bytes": int(max_bytes),
        "max_records": int(max_records),
    }


class segmented_provider_pressure_event_writer:
    def __init__(
        self,
        store: segmented_provider_pressure_store,
        *,
        durability_batch_records: int,
        on_segment_sealed: Callable[[Mapping[str, Any]], None] | None,
    ) -> None:
        if durability_batch_records <= 0:
            raise provider_pressure_store_v2_error("durability_batch_records must be positive.")
        self.store = store
        self.durability_batch_records = int(durability_batch_records)
        self.on_segment_sealed = on_segment_sealed
        self._lock_context = None
        self._lineage: dict[str, Any] | None = None
        self._handle = None
        self._pending_records = 0

    def __enter__(self) -> "segmented_provider_pressure_event_writer":
        self._lock_context = self.store.locked()
        self._lock_context.__enter__()
        lineage = self.store._load_lineage_unlocked()
        self.store._ensure_segment_policy_unlocked(lineage)
        lineage = self.store._load_lineage_unlocked()
        self.store._recover_active_segment_unlocked(lineage)
        self._lineage = self.store._load_lineage_unlocked()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            self.commit()
        finally:
            self._close_handle()
            if self._lock_context is not None:
                self._lock_context.__exit__(exc_type, exc, tb)
        return False

    @property
    def segment_policy(self) -> Mapping[str, Any]:
        if self._lineage is None:
            raise provider_pressure_store_v2_error("Provider Pressure event writer is not open.")
        return self.store._segment_policy_unlocked(self._lineage)

    def append_event(
        self,
        *,
        kind: str,
        execution_id: str,
        payload: Mapping[str, Any],
        created_at: datetime,
    ) -> int:
        lineage = self._require_lineage()
        sequence = int(lineage["latest_event_sequence"]) + 1
        parent_sha256 = lineage.get("latest_event_sha256")
        payload_record = self.store._payload_record_unlocked(payload)
        event_meta = {
            "sequence": sequence,
            "created_at": _iso(created_at),
            "kind": str(kind),
            "parent_sha256": parent_sha256,
        }
        body: dict[str, Any] = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event": event_meta,
            "execution_id": str(execution_id),
            **payload_record,
        }
        digest = _json_digest(body)
        event_meta["event_sha256"] = digest
        line = (json.dumps(body, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
        policy = self.store._segment_policy_unlocked(lineage)
        if len(line) > int(policy["max_bytes"]):
            raise provider_pressure_store_v2_error(
                "Provider Pressure event metadata exceeds the segment byte bound even after payload externalization."
            )
        self._rotate_if_needed(len(line))
        lineage = self._require_lineage()
        handle = self._ensure_handle()
        handle.write(line)
        lineage["latest_event_sequence"] = sequence
        lineage["latest_event_sha256"] = digest
        lineage["active_segment_record_count"] = int(lineage["active_segment_record_count"]) + 1
        lineage["active_segment_bytes"] = int(lineage["active_segment_bytes"]) + len(line)
        lineage["updated_at"] = _iso(created_at)
        self._pending_records += 1
        if self._pending_records >= self.durability_batch_records:
            self.commit()
        return sequence

    def commit(self) -> None:
        if self._lineage is None or self._pending_records <= 0:
            return
        if self._handle is not None:
            self._handle.flush()
            os.fsync(self._handle.fileno())
        _write_json_atomically(self.store.lineage_path, self._lineage)
        self._pending_records = 0

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        lineage = self._require_lineage()
        count = int(lineage.get("active_segment_record_count", 0))
        size = int(lineage.get("active_segment_bytes", 0))
        if count <= 0:
            return
        policy = self.store._segment_policy_unlocked(lineage)
        if count < int(policy["max_records"]) and size + incoming_bytes <= int(policy["max_bytes"]):
            return
        self.commit()
        self._close_handle()
        index = int(lineage["active_segment_index"])
        self.store._seal_active_segment_unlocked(lineage)
        self._lineage = self.store._load_lineage_unlocked()
        if self.on_segment_sealed is not None:
            meta = _load_json_object(self.store._segment_meta_path(index))
            self.on_segment_sealed(meta)

    def _ensure_handle(self):
        if self._handle is None:
            lineage = self._require_lineage()
            path = self.store._segment_path(int(lineage["active_segment_index"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = path.open("ab")
        return self._handle

    def _close_handle(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _require_lineage(self) -> dict[str, Any]:
        if self._lineage is None:
            raise provider_pressure_store_v2_error("Provider Pressure event writer is not open.")
        return self._lineage

def _json_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise provider_pressure_store_v2_error(f"Could not read JSON artifact '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise provider_pressure_store_v2_error(f"JSON artifact '{path}' is not an object.")
    return value


def _write_json_atomically(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoded = (json.dumps(dict(value), ensure_ascii=True, sort_keys=False, indent=2) + "\n").encode("ascii")
    try:
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_yaml_atomically(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            yaml.safe_dump(
                dict(value),
                stream=handle,
                sort_keys=False,
                allow_unicode=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        size = temporary.stat().st_size
        if size > OPERATIONAL_PROFILE_MAX_BYTES:
            raise provider_pressure_store_v2_error(
                f"Provider Pressure operational profile would be {size} bytes; "
                "provider model state must be compacted before persistence."
            )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _iso(value: datetime) -> str:
    current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _segment_index(path: Path) -> int:
    stem = path.name.removeprefix("segment_").removesuffix(".jsonl")
    try:
        return int(stem)
    except ValueError as exc:
        raise provider_pressure_store_v2_error(f"Malformed Provider Pressure segment filename '{path.name}'.") from exc
