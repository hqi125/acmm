from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping, Protocol

import yaml
from jsonschema import Draft202012Validator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows callers do not share WSL runtime state.
    fcntl = None


PROVIDER_PRESSURE_PROFILE_SCHEMA_VERSION = "0.5"
PROVIDER_PRESSURE_TRAJECTORY_SCHEMA_VERSION = "0.5"
PROVIDER_PRESSURE_INDEX_SCHEMA_VERSION = "0.1"
PROVIDER_PRESSURE_ROOT_NAME = "provider_pressure"
_PROFILE_SCHEMA_NAME = "provider-pressure-profile-v0.5.schema.json"
_TRAJECTORY_SCHEMA_NAME = "provider-pressure-trajectory-v0.5.schema.json"
_INDEX_SCHEMA_NAME = "provider-pressure-index-v0.1.schema.json"

_LOCAL_LOCKS_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.RLock] = {}
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class runtime_provider_pressure_identity:
    provider_id: str
    scope_id: str
    model_id: str
    model_version: str
    model_definition_sha256: str

    def __post_init__(self) -> None:
        for label, value in (
            ("provider_id", self.provider_id),
            ("scope_id", self.scope_id),
            ("model_id", self.model_id),
            ("model_version", self.model_version),
        ):
            if not str(value).strip():
                raise ValueError(f"Provider Pressure {label} must not be empty.")
        if not re.fullmatch(r"[0-9a-f]{64}", self.model_definition_sha256):
            raise ValueError(
                "Provider Pressure model_definition_sha256 must be a lowercase SHA-256."
            )


@dataclass(frozen=True)
class runtime_provider_pressure_policy:
    baseline_score: float = 1.0
    punishment_increment: float = 1.5
    reward_decrement: float = 0.04
    score_ceiling: float = 8.0
    admission_free_score: float = 1.0
    base_delay_seconds: float = 30.0
    max_delay_seconds: float = 600.0
    confidence_observation_scale: int = 20

    def __post_init__(self) -> None:
        numeric = {
            "baseline_score": self.baseline_score,
            "punishment_increment": self.punishment_increment,
            "reward_decrement": self.reward_decrement,
            "score_ceiling": self.score_ceiling,
            "admission_free_score": self.admission_free_score,
            "base_delay_seconds": self.base_delay_seconds,
            "max_delay_seconds": self.max_delay_seconds,
        }
        if any(not isinstance(value, (int, float)) for value in numeric.values()):
            raise TypeError("Provider Pressure policy numeric fields must be numbers.")
        if self.baseline_score <= 0:
            raise ValueError("Provider Pressure baseline_score must be positive.")
        if self.score_ceiling < self.baseline_score:
            raise ValueError("Provider Pressure score_ceiling cannot be below baseline.")
        if self.admission_free_score < self.baseline_score:
            raise ValueError("Provider Pressure admission_free_score cannot be below baseline.")
        if self.punishment_increment < 0 or self.reward_decrement < 0:
            raise ValueError("Provider Pressure feature learning increments cannot be negative.")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("Provider Pressure admission delays cannot be negative.")
        if self.confidence_observation_scale < 1:
            raise ValueError("Provider Pressure confidence scale must be positive.")


@dataclass(frozen=True)
class runtime_provider_pressure_app_policy:
    enabled: bool = False
    actions: tuple[str, ...] = ()
    max_recent_probes: int = 64

    def __post_init__(self) -> None:
        normalized = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in self.actions
                if str(item).strip()
            )
        )
        if self.enabled and not normalized:
            raise ValueError(
                "Provider Pressure APP policy must mark at least one action when enabled."
            )
        if self.max_recent_probes < 1:
            raise ValueError("Provider Pressure APP recent-probe bound must be positive.")
        object.__setattr__(self, "actions", normalized)

    def eligible(self, action_id: str) -> bool:
        rendered = str(action_id).strip()
        return self.enabled and any(
            fnmatchcase(rendered, pattern)
            for pattern in self.actions
        )


@dataclass(frozen=True)
class runtime_provider_pressure_execution_context:
    experiment_id: str
    execution_id: str
    selected_run_indices: tuple[int, ...]
    session_dir: str
    generated_root: str
    package_sha256: str | None = None
    progress: Callable[[str], None] | None = field(
        default=None,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True)
class runtime_provider_pressure_run_context:
    experiment_id: str
    execution_id: str
    run_index: int
    subject_id: str
    scenario_id: str
    repetition: int
    materialization_key: str
    session_dir: str
    progress: Callable[[str], None] | None = field(
        default=None,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True)
class runtime_provider_pressure_run_outcome:
    completed: bool
    resolved: bool
    attempt_count: int
    terminations: tuple[str, ...] = ()
    detail: str | None = None


@dataclass(frozen=True)
class runtime_provider_pressure_workload:
    features: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized: dict[str, float] = {}
        for key, raw in self.features.items():
            name = str(key).strip()
            if not name:
                raise ValueError("Provider Pressure workload feature names must not be empty.")
            value = float(raw)
            if value < 0:
                raise ValueError("Provider Pressure workload feature weights cannot be negative.")
            if value > 0:
                normalized[name] = value
        object.__setattr__(self, "features", MappingProxyType(normalized))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class runtime_provider_pressure_pacing_event:
    source: str
    reason: str
    duration_seconds: float
    action_id: str
    features: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source not in {"pph", "runtime"}:
            raise ValueError("Provider Pressure pacing source must be pph or runtime.")
        if not str(self.reason).strip():
            raise ValueError("Provider Pressure pacing reason must not be empty.")
        if not str(self.action_id).strip():
            raise ValueError("Provider Pressure pacing action_id must not be empty.")
        if self.duration_seconds < 0:
            raise ValueError("Provider Pressure pacing duration cannot be negative.")
        normalized: dict[str, float] = {}
        for key, raw in self.features.items():
            name = str(key).strip()
            if not name:
                raise ValueError("Provider Pressure pacing feature names must not be empty.")
            value = float(raw)
            if value < 0:
                raise ValueError("Provider Pressure pacing feature weights cannot be negative.")
            if value > 0:
                normalized[name] = value
        object.__setattr__(self, "features", MappingProxyType(normalized))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class runtime_provider_pressure_recovery_event:
    kind: str
    wait_seconds: float
    recommendation_source: str
    action_id: str
    decision_id: str | None = None
    features: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    admission_wait_seconds: float = 0.0
    underprediction_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.kind not in {"primary", "secondary"}:
            raise ValueError(
                "Provider Pressure recovery kind must be primary or secondary."
            )
        if self.wait_seconds < 0:
            raise ValueError("Provider Pressure recovery wait cannot be negative.")
        if not str(self.recommendation_source).strip():
            raise ValueError(
                "Provider Pressure recovery recommendation source must not be empty."
            )
        if not str(self.action_id).strip():
            raise ValueError("Provider Pressure recovery action_id must not be empty.")
        if self.decision_id is not None and not str(self.decision_id).strip():
            raise ValueError(
                "Provider Pressure recovery decision_id must be non-empty when provided."
            )
        if self.admission_wait_seconds < 0:
            raise ValueError(
                "Provider Pressure recovery admission wait cannot be negative."
            )
        if self.underprediction_seconds < 0:
            raise ValueError(
                "Provider Pressure recovery underprediction cannot be negative."
            )
        normalized: dict[str, float] = {}
        for key, raw in self.features.items():
            name = str(key).strip()
            if not name:
                raise ValueError(
                    "Provider Pressure recovery feature names must not be empty."
                )
            value = float(raw)
            if value < 0:
                raise ValueError(
                    "Provider Pressure recovery feature weights cannot be negative."
                )
            if value > 0:
                normalized[name] = value
        object.__setattr__(self, "features", MappingProxyType(normalized))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class runtime_provider_pressure_app_suspension:
    duration_seconds: float
    reason: str = "adaptive_pressure_pocket"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.duration_seconds, (int, float)) or self.duration_seconds < 0:
            raise ValueError(
                "Provider Pressure APP suspension duration must be non-negative."
            )
        if not str(self.reason).strip():
            raise ValueError(
                "Provider Pressure APP suspension reason must not be empty."
            )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class runtime_provider_pressure_action_result_event:
    outcome: str
    action_id: str
    decision_id: str | None = None
    features: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome not in {"success", "failure", "interrupted"}:
            raise ValueError(
                "Provider Pressure action result must be success, failure, or interrupted."
            )
        if not str(self.action_id).strip():
            raise ValueError("Provider Pressure action result action_id must not be empty.")
        if self.decision_id is not None and not str(self.decision_id).strip():
            raise ValueError(
                "Provider Pressure action result decision_id must be non-empty when provided."
            )
        normalized: dict[str, float] = {}
        for key, raw in self.features.items():
            name = str(key).strip()
            if not name:
                raise ValueError(
                    "Provider Pressure action result feature names must not be empty."
                )
            value = float(raw)
            if value < 0:
                raise ValueError(
                    "Provider Pressure action result feature weights cannot be negative."
                )
            if value > 0:
                normalized[name] = value
        object.__setattr__(self, "features", MappingProxyType(normalized))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))




@dataclass(frozen=True)
class runtime_provider_pressure_transport_request:
    plane: str
    method: str
    url: str
    request_kind: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.plane).strip():
            raise ValueError("Provider Pressure transport plane must not be empty.")
        if not str(self.method).strip():
            raise ValueError("Provider Pressure transport method must not be empty.")
        if not str(self.url).strip():
            raise ValueError("Provider Pressure transport URL must not be empty.")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class runtime_provider_pressure_transport_action:
    action_id: str
    workload: runtime_provider_pressure_workload
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.action_id).strip():
            raise ValueError("Provider Pressure transport action id must not be empty.")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class runtime_provider_pressure_transport_response:
    status: int | None
    headers: Mapping[str, str] = field(default_factory=dict)
    body: str = ""
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status is not None and (self.status < 100 or self.status > 599):
            raise ValueError("Provider Pressure transport status is invalid.")
        object.__setattr__(
            self,
            "headers",
            MappingProxyType({str(k): str(v) for k, v in self.headers.items()}),
        )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class runtime_provider_pressure_transport_recovery:
    kind: str
    wait_seconds: float
    recommendation_source: str
    retry: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in {"primary", "secondary"}:
            raise ValueError("Provider Pressure transport recovery kind is invalid.")
        if self.wait_seconds < 0:
            raise ValueError("Provider Pressure transport recovery wait cannot be negative.")
        if not str(self.recommendation_source).strip():
            raise ValueError("Provider Pressure transport recovery source must not be empty.")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class runtime_provider_pressure_gate_configuration:
    control_path: str
    bind_host: str = "0.0.0.0"
    bind_port: int = 8765
    advertised_url: str = "http://host.docker.internal:8765"

    def __post_init__(self) -> None:
        if not str(self.control_path).strip():
            raise ValueError("Provider Pressure gate control path must not be empty.")
        if not str(self.bind_host).strip():
            raise ValueError("Provider Pressure gate bind host must not be empty.")
        if not isinstance(self.bind_port, int) or isinstance(self.bind_port, bool):
            raise TypeError("Provider Pressure gate port must be an integer.")
        if self.bind_port < 1 or self.bind_port > 65535:
            raise ValueError("Provider Pressure gate port is outside the valid range.")
        if not str(self.advertised_url).strip():
            raise ValueError("Provider Pressure gate advertised URL must not be empty.")


@dataclass(frozen=True)
class runtime_provider_pressure_signal:
    kind: str
    count: int
    features: Mapping[str, float] = field(default_factory=dict)
    severity: float = 1.0
    pressure_duration_seconds: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in {"reward", "punishment"}:
            raise ValueError("Provider Pressure signal kind must be reward or punishment.")
        if not isinstance(self.count, int) or isinstance(self.count, bool) or self.count < 1:
            raise ValueError("Provider Pressure signal count must be a positive integer.")
        if self.severity <= 0:
            raise ValueError("Provider Pressure signal severity must be positive.")
        if self.pressure_duration_seconds < 0:
            raise ValueError(
                "Provider Pressure pressure_duration_seconds must not be negative."
            )
        normalized: dict[str, float] = {}
        for key, raw in self.features.items():
            name = str(key).strip()
            if not name:
                raise ValueError("Provider Pressure signal feature names must not be empty.")
            value = float(raw)
            if value < 0:
                raise ValueError("Provider Pressure signal feature weights cannot be negative.")
            if value > 0:
                normalized[name] = value
        object.__setattr__(self, "features", MappingProxyType(normalized))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class runtime_provider_pressure_observation:
    source_kind: str
    source_execution_id: str
    signals: tuple[runtime_provider_pressure_signal, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source_kind not in {"historical_replay", "live_execution"}:
            raise ValueError(
                "Provider Pressure observation source_kind must be historical_replay "
                "or live_execution."
            )
        if not self.source_execution_id.strip():
            raise ValueError("Provider Pressure source execution ID must not be empty.")
        object.__setattr__(self, "signals", tuple(self.signals))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class runtime_provider_pressure_model(Protocol):
    def identity(
        self,
        context: runtime_provider_pressure_execution_context,
    ) -> runtime_provider_pressure_identity:
        ...

    def policy(
        self,
        context: runtime_provider_pressure_execution_context,
    ) -> runtime_provider_pressure_policy:
        ...

    def app_policy(
        self,
        context: runtime_provider_pressure_execution_context,
    ) -> runtime_provider_pressure_app_policy:
        ...

    def app_admission_suspension(
        self,
        context: runtime_provider_pressure_run_context,
        action_id: str,
        workload: runtime_provider_pressure_workload,
        model_state: Mapping[str, Any],
    ) -> runtime_provider_pressure_app_suspension:
        ...

    def app_probe_recovery_metadata(
        self,
        context: runtime_provider_pressure_run_context,
        event: runtime_provider_pressure_recovery_event,
        probe: Mapping[str, Any],
        model_state: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    def app_probe_close(
        self,
        context: runtime_provider_pressure_run_context,
        event: runtime_provider_pressure_action_result_event,
        probe: Mapping[str, Any],
        model_state: dict[str, Any],
    ) -> Mapping[str, Any]:
        ...

    def app_safe_observe(
        self,
        context: runtime_provider_pressure_run_context,
        event: runtime_provider_pressure_action_result_event,
        model_state: dict[str, Any],
    ) -> Mapping[str, Any]:
        ...

    def transport_gate_configuration(
        self,
        context: runtime_provider_pressure_execution_context,
    ) -> runtime_provider_pressure_gate_configuration | None:
        ...

    def transport_action(
        self,
        context: runtime_provider_pressure_run_context,
        request: runtime_provider_pressure_transport_request,
    ) -> runtime_provider_pressure_transport_action:
        ...

    def transport_recovery(
        self,
        context: runtime_provider_pressure_run_context,
        request: runtime_provider_pressure_transport_request,
        response: runtime_provider_pressure_transport_response,
    ) -> runtime_provider_pressure_transport_recovery | None:
        ...

    def historical_replay(
        self,
        context: runtime_provider_pressure_execution_context,
    ) -> tuple[runtime_provider_pressure_observation, ...]:
        ...

    def run_workload(
        self,
        context: runtime_provider_pressure_run_context,
    ) -> runtime_provider_pressure_workload:
        ...

    def run_measurement_start(
        self,
        context: runtime_provider_pressure_run_context,
    ) -> Any:
        ...

    def pacing_observe(
        self,
        context: runtime_provider_pressure_run_context,
        event: runtime_provider_pressure_pacing_event,
    ) -> runtime_provider_pressure_observation:
        ...

    def recovery_observe(
        self,
        context: runtime_provider_pressure_run_context,
        event: runtime_provider_pressure_recovery_event,
    ) -> runtime_provider_pressure_observation:
        ...

    def run_observe(
        self,
        context: runtime_provider_pressure_run_context,
        measurement_handle: Any,
        outcome: runtime_provider_pressure_run_outcome,
    ) -> runtime_provider_pressure_observation:
        ...


@dataclass
class provider_pressure_execution_session:
    model: runtime_provider_pressure_model
    context: runtime_provider_pressure_execution_context
    identity: runtime_provider_pressure_identity
    policy: runtime_provider_pressure_policy
    store: "provider_pressure_store"
    profile_before: dict[str, Any]
    profile: dict[str, Any]
    execution_signals: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    pacing_events: list[dict[str, Any]] = field(default_factory=list)
    applied_historical_sources: list[str] = field(default_factory=list)
    baseline_last_request_at: datetime | None = None
    baseline_last_mutation_at: datetime | None = None


@dataclass(frozen=True)
class provider_pressure_run_admission:
    context: runtime_provider_pressure_run_context
    workload: runtime_provider_pressure_workload
    measurement_handle: Any
    record: Mapping[str, Any]


@dataclass(frozen=True)
class provider_pressure_action_admission:
    context: runtime_provider_pressure_run_context
    action_id: str
    workload: runtime_provider_pressure_workload
    record: Mapping[str, Any]


@dataclass(frozen=True)
class provider_pressure_recovery_admission:
    context: runtime_provider_pressure_run_context
    event: runtime_provider_pressure_recovery_event
    record: Mapping[str, Any]


def _runtime_app_policy(
    session: provider_pressure_execution_session,
) -> runtime_provider_pressure_app_policy:
    getter = getattr(session.model, "app_policy", None)
    if not callable(getter):
        return runtime_provider_pressure_app_policy()
    value = getter(session.context)
    if not isinstance(value, runtime_provider_pressure_app_policy):
        raise TypeError(
            "Provider Pressure app_policy() must return runtime_provider_pressure_app_policy."
        )
    return value


def _core_app_state_mutable(model_state: dict[str, Any]) -> dict[str, Any]:
    value = model_state.get("__core_app")
    if not isinstance(value, dict):
        value = {}
        model_state["__core_app"] = value
    value.setdefault("schema_version", "0.1")
    value.setdefault("next_probe_sequence", 0)
    if not isinstance(value.get("open_probes"), dict):
        value["open_probes"] = {}
    if not isinstance(value.get("recent_probes"), list):
        value["recent_probes"] = []
    return value


class provider_pressure_store:
    def __init__(
        self,
        generated_root: Path,
        identity: runtime_provider_pressure_identity,
    ) -> None:
        scope_hash = hashlib.sha256(identity.scope_id.encode("utf-8")).hexdigest()
        lineage = (
            f"{_safe_component(identity.model_version)}-"
            f"{identity.model_definition_sha256[:12]}"
        )
        self.root = (
            Path(generated_root)
            / PROVIDER_PRESSURE_ROOT_NAME
            / _safe_component(identity.provider_id)
            / scope_hash
            / _safe_component(identity.model_id)
            / lineage
        )
        self.profile_path = self.root / "profile-state.yaml"
        self.index_path = self.root / "trajectory-index.yaml"
        self.trajectory_dir = self.root / "trajectory"
        self.lock_path = self.root / ".trajectory.lock"
        lock_key = str(self.lock_path)
        with _LOCAL_LOCKS_GUARD:
            self._local_lock = _LOCAL_LOCKS.setdefault(lock_key, threading.RLock())

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

    def load_profile(
        self,
        identity: runtime_provider_pressure_identity,
        policy: runtime_provider_pressure_policy,
        now: datetime,
    ) -> tuple[dict[str, Any], bool]:
        with self.locked():
            if self.profile_path.is_file():
                try:
                    value = yaml.safe_load(self.profile_path.read_text(encoding="utf-8"))
                except (OSError, yaml.YAMLError):
                    value = None
                if isinstance(value, Mapping) and _profile_identity_matches(value, identity):
                    upgraded = _upgrade_profile_document(value)
                    _validate_document(_PROFILE_SCHEMA_NAME, upgraded)
                    if dict(upgraded) != dict(value):
                        self._persist_profile_unlocked(upgraded)
                    return _copy_mapping(upgraded), False
            profile = _new_profile(identity, policy, now)
            self._persist_profile_unlocked(profile)
            self._append_snapshot_unlocked(
                profile_before=profile,
                profile_after=profile,
                identity=identity,
                source_kind="bootstrap",
                source_execution_id="bootstrap",
                execution_status="bootstrap",
                applied_signals=(),
                decisions=(),
                source_metadata={},
                created_at=now,
            )
            return _copy_mapping(profile), True

    def persist_profile(self, profile: Mapping[str, Any]) -> None:
        with self.locked():
            self._persist_profile_unlocked(profile)

    def append_snapshot(
        self,
        *,
        profile_before: Mapping[str, Any],
        profile_after: Mapping[str, Any],
        identity: runtime_provider_pressure_identity,
        source_kind: str,
        source_execution_id: str,
        execution_status: str,
        applied_signals: tuple[Mapping[str, Any], ...],
        decisions: tuple[Mapping[str, Any], ...],
        source_metadata: Mapping[str, Any] | None,
        created_at: datetime,
    ) -> Path:
        with self.locked():
            return self._append_snapshot_unlocked(
                profile_before=profile_before,
                profile_after=profile_after,
                identity=identity,
                source_kind=source_kind,
                source_execution_id=source_execution_id,
                execution_status=execution_status,
                applied_signals=applied_signals,
                decisions=decisions,
                source_metadata=source_metadata,
                created_at=created_at,
            )

    def _persist_profile_unlocked(self, profile: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _validate_document(_PROFILE_SCHEMA_NAME, profile)
        _write_yaml_atomically(self.profile_path, profile)

    def _append_snapshot_unlocked(
        self,
        *,
        profile_before: Mapping[str, Any],
        profile_after: Mapping[str, Any],
        identity: runtime_provider_pressure_identity,
        source_kind: str,
        source_execution_id: str,
        execution_status: str,
        applied_signals: tuple[Mapping[str, Any], ...],
        decisions: tuple[Mapping[str, Any], ...],
        source_metadata: Mapping[str, Any] | None,
        created_at: datetime,
    ) -> Path:
        self.trajectory_dir.mkdir(parents=True, exist_ok=True)
        index = self._load_index_unlocked(identity)
        sequence = int(index.get("latest_sequence", -1)) + 1
        parent_sha256 = index.get("latest_snapshot_sha256")
        timestamp = _format_timestamp(created_at)
        source_label = _safe_component(source_kind)
        execution_label = _safe_component(source_execution_id)[:48]
        filename = f"{sequence:06d}_{timestamp}_{source_label}_{execution_label}.yaml"
        snapshot_path = self.trajectory_dir / filename
        snapshot_body = {
            "schema_version": PROVIDER_PRESSURE_TRAJECTORY_SCHEMA_VERSION,
            "snapshot": {
                "sequence": sequence,
                "created_at": _iso(created_at),
                "source_kind": source_kind,
                "source_execution_id": source_execution_id,
                "execution_status": execution_status,
                "parent_sha256": parent_sha256,
            },
            "identity": _identity_record(identity),
            "source_metadata": dict(source_metadata or {}),
            "profile_before": _profile_projection(profile_before),
            "learning_delta": {
                "signals": [dict(item) for item in applied_signals],
                "decisions": [dict(item) for item in decisions],
                "pacing_events": [dict(item) for item in (source_metadata or {}).get("pacing_events", ())],
            },
            "profile_after": _profile_projection(profile_after),
        }
        digest = _document_sha256(snapshot_body)
        snapshot_body["snapshot"]["snapshot_sha256"] = digest
        _validate_document(_TRAJECTORY_SCHEMA_NAME, snapshot_body)
        _write_yaml_atomically(snapshot_path, snapshot_body)

        entries = list(index.get("snapshots", ()))
        entries.append(
            {
                "sequence": sequence,
                "path": str(Path("trajectory") / filename),
                "created_at": _iso(created_at),
                "source_kind": source_kind,
                "source_execution_id": source_execution_id,
                "snapshot_sha256": digest,
                "parent_sha256": parent_sha256,
            }
        )
        updated_index = {
            "schema_version": PROVIDER_PRESSURE_INDEX_SCHEMA_VERSION,
            "identity": _identity_record(identity),
            "latest_sequence": sequence,
            "latest_snapshot": str(Path("trajectory") / filename),
            "latest_snapshot_sha256": digest,
            "snapshots": entries,
        }
        _validate_document(_INDEX_SCHEMA_NAME, updated_index)
        _write_yaml_atomically(self.index_path, updated_index)
        return snapshot_path

    def _load_index_unlocked(
        self,
        identity: runtime_provider_pressure_identity,
    ) -> dict[str, Any]:
        if self.index_path.is_file():
            try:
                value = yaml.safe_load(self.index_path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                value = None
            if isinstance(value, Mapping):
                _validate_document(_INDEX_SCHEMA_NAME, value)
                return _copy_mapping(value)
        return {
            "schema_version": PROVIDER_PRESSURE_INDEX_SCHEMA_VERSION,
            "identity": _identity_record(identity),
            "latest_sequence": -1,
            "latest_snapshot": None,
            "latest_snapshot_sha256": None,
            "snapshots": [],
        }


class provider_pressure_engine:
    def __init__(
        self,
        model: runtime_provider_pressure_model,
        *,
        sleep: Callable[[float], None],
        now: Callable[[], datetime],
    ) -> None:
        self._model = model
        self._sleep = sleep
        self._now = now

    def execution_initialize(
        self,
        context: runtime_provider_pressure_execution_context,
    ) -> provider_pressure_execution_session:
        identity = self._model.identity(context)
        policy = self._model.policy(context)
        store = provider_pressure_store(Path(context.generated_root), identity)
        profile, _ = store.load_profile(identity, policy, self._now())
        session = provider_pressure_execution_session(
            model=self._model,
            context=context,
            identity=identity,
            policy=policy,
            store=store,
            profile_before=_copy_mapping(profile),
            profile=_copy_mapping(profile),
        )
        applied_sources = set(str(value) for value in profile.get("applied_sources", ()))
        applied_execution_ids = {
            value.split(":", 1)[1]
            for value in applied_sources
            if ":" in value
        }
        observations = tuple(self._model.historical_replay(context))
        for observation in observations:
            source_key = f"{observation.source_kind}:{observation.source_execution_id}"
            if (
                source_key in applied_sources
                or observation.source_execution_id in applied_execution_ids
            ):
                continue
            before = _copy_mapping(session.profile)
            signal_records = self._apply_observation(session, observation)
            session.profile["applied_sources"] = [
                *list(session.profile.get("applied_sources", ())),
                source_key,
            ]
            session.profile["updated_at"] = _iso(self._now())
            session.store.persist_profile(session.profile)
            session.store.append_snapshot(
                profile_before=before,
                profile_after=session.profile,
                identity=session.identity,
                source_kind="historical_replay",
                source_execution_id=observation.source_execution_id,
                execution_status="historical_replay",
                applied_signals=tuple(signal_records),
                decisions=(),
                source_metadata=dict(observation.metadata),
                created_at=self._now(),
            )
            session.applied_historical_sources.append(observation.source_execution_id)
            applied_sources.add(source_key)
            applied_execution_ids.add(observation.source_execution_id)
        session.profile_before = _copy_mapping(session.profile)
        return session

    def run_admit(
        self,
        session: provider_pressure_execution_session,
        context: runtime_provider_pressure_run_context,
    ) -> provider_pressure_run_admission:
        workload = session.model.run_workload(context)
        pressure = _workload_pressure(session.profile, workload, session.policy)
        record = {
            "decision_kind": "run_measurement",
            "run_index": context.run_index,
            "scenario_id": context.scenario_id,
            "subject_id": context.subject_id,
            "features": dict(workload.features),
            "effective_pressure": pressure,
            "recommended_wait_seconds": 0.0,
            "profile_sequence": int(session.profile.get("sequence", 0)),
            "metadata": dict(workload.metadata),
        }
        measurement_handle = session.model.run_measurement_start(context)
        session.decisions.append(dict(record))
        return provider_pressure_run_admission(
            context=context,
            workload=workload,
            measurement_handle=measurement_handle,
            record=MappingProxyType(record),
        )

    def action_admit(
        self,
        session: provider_pressure_execution_session,
        context: runtime_provider_pressure_run_context,
        *,
        action_id: str,
        workload: runtime_provider_pressure_workload,
    ) -> provider_pressure_action_admission:
        rendered_action = str(action_id).strip()
        if not rendered_action:
            raise ValueError("Provider Pressure action id must not be empty.")

        event_pressure = _workload_pressure(session.profile, workload, session.policy)
        event_delay = min(
            session.policy.max_delay_seconds,
            max(
                0.0,
                (event_pressure - session.policy.admission_free_score)
                * session.policy.base_delay_seconds,
            ),
        )
        app_policy = _runtime_app_policy(session)
        app_eligible = app_policy.eligible(rendered_action)
        app_suspension_seconds = 0.0
        app_suspension_metadata: dict[str, Any] = {}
        app_suspension_reason = "adaptive_pressure_pocket"
        app_admission_suspension = getattr(session.model, "app_admission_suspension", None)
        if app_eligible and callable(app_admission_suspension):
            suspension = app_admission_suspension(
                context,
                rendered_action,
                workload,
                MappingProxyType(_copy_mapping(session.profile.get("model_state", {}))),
            )
            if not isinstance(suspension, runtime_provider_pressure_app_suspension):
                raise TypeError(
                    "Provider Pressure app_admission_suspension() must return "
                    "runtime_provider_pressure_app_suspension."
                )
            app_suspension_seconds = max(0.0, float(suspension.duration_seconds))
            app_suspension_reason = str(suspension.reason)
            app_suspension_metadata = dict(suspension.metadata)

        decision_ordinal = sum(
            1
            for item in session.decisions
            if item.get("decision_kind") == "action"
        )
        decision_id = (
            f"{context.execution_id}:run={context.run_index}:"
            f"action={decision_ordinal:04d}"
        )

        if app_suspension_seconds > 0:
            pacing = runtime_provider_pressure_pacing_event(
                source="pph",
                reason="adaptive_pressure_pocket_suspension",
                duration_seconds=app_suspension_seconds,
                action_id=rendered_action,
                features=workload.features,
                metadata={
                    "run_index": context.run_index,
                    "scenario_id": context.scenario_id,
                    "subject_id": context.subject_id,
                    "decision_id": decision_id,
                    **app_suspension_metadata,
                },
            )
            self._record_pacing_event(session, pacing)
            if context.progress is not None:
                context.progress(
                    "APP: pressure pocket for "
                    + rendered_action
                    + ", suspending ~"
                    + _approximate_duration(app_suspension_seconds)
                    + "."
                )
            self._sleep(app_suspension_seconds)

        if event_delay > 0:
            pacing = runtime_provider_pressure_pacing_event(
                source="pph",
                reason="learned_provider_pressure",
                duration_seconds=event_delay,
                action_id=rendered_action,
                features=workload.features,
                metadata={
                    "run_index": context.run_index,
                    "scenario_id": context.scenario_id,
                    "subject_id": context.subject_id,
                    "decision_id": decision_id,
                    **dict(workload.metadata),
                },
            )
            self._record_pacing_event(session, pacing)
            # Event-PPH pacing remains quiet unless the experiment explicitly
            # selects the action for console diagnostics. Provider recovery that
            # proves this wait insufficient is always surfaced.
            self._sleep(event_delay)

        baseline_request_interval = max(
            0.0,
            float(workload.metadata.get("baseline_request_interval_seconds", 0.0) or 0.0),
        )
        baseline_mutation_interval = max(
            0.0,
            float(workload.metadata.get("baseline_mutation_interval_seconds", 0.0) or 0.0),
        )
        baseline_mutation = bool(workload.metadata.get("baseline_mutation", False))
        baseline_wait = 0.0
        request_elapsed: float | None = None
        mutation_elapsed: float | None = None
        request_remaining = 0.0
        mutation_remaining = 0.0
        now = self._now()
        if baseline_request_interval > 0 and session.baseline_last_request_at is not None:
            request_elapsed = max(
                0.0,
                (now - session.baseline_last_request_at).total_seconds(),
            )
            request_remaining = max(
                0.0,
                baseline_request_interval - request_elapsed,
            )
            baseline_wait = max(baseline_wait, request_remaining)
        if (
            baseline_mutation
            and baseline_mutation_interval > 0
            and session.baseline_last_mutation_at is not None
        ):
            mutation_elapsed = max(
                0.0,
                (now - session.baseline_last_mutation_at).total_seconds(),
            )
            mutation_remaining = max(
                0.0,
                baseline_mutation_interval - mutation_elapsed,
            )
            baseline_wait = max(baseline_wait, mutation_remaining)
        baseline_wait = max(0.0, baseline_wait)

        if baseline_mutation and baseline_mutation_interval >= baseline_request_interval:
            spacing_target = baseline_mutation_interval
            spacing_elapsed = mutation_elapsed
        else:
            spacing_target = baseline_request_interval
            spacing_elapsed = request_elapsed

        if mutation_remaining > request_remaining:
            spacing_target = baseline_mutation_interval
            spacing_elapsed = mutation_elapsed
        elif request_remaining > mutation_remaining:
            spacing_target = baseline_request_interval
            spacing_elapsed = request_elapsed
        if baseline_wait > 0:
            pacing = runtime_provider_pressure_pacing_event(
                source="pph",
                reason="provider_baseline_spacing",
                duration_seconds=baseline_wait,
                action_id=rendered_action,
                features=workload.features,
                metadata={
                    "run_index": context.run_index,
                    "scenario_id": context.scenario_id,
                    "subject_id": context.subject_id,
                    "decision_id": decision_id,
                    "request_interval_seconds": baseline_request_interval,
                    "mutation_interval_seconds": baseline_mutation_interval,
                    "mutation": baseline_mutation,
                    **dict(workload.metadata),
                },
            )
            self._record_pacing_event(session, pacing)
            self._sleep(baseline_wait)

        admitted_at = self._now()
        session.baseline_last_request_at = admitted_at
        if baseline_mutation:
            session.baseline_last_mutation_at = admitted_at

        total_wait = app_suspension_seconds + event_delay + baseline_wait
        record = {
            "decision_kind": "action",
            "decision_id": decision_id,
            "run_index": context.run_index,
            "scenario_id": context.scenario_id,
            "subject_id": context.subject_id,
            "action_id": rendered_action,
            "features": dict(workload.features),
            "effective_pressure": event_pressure,
            "event_pph_pressure": event_pressure,
            "event_pph_wait_seconds": event_delay,
            "app_suspension_seconds": app_suspension_seconds,
            "baseline_spacing_wait_seconds": baseline_wait,
            "baseline_spacing_target_seconds": spacing_target,
            "baseline_spacing_elapsed_seconds": spacing_elapsed,
            "total_admission_wait_seconds": total_wait,
            "recommended_wait_seconds": total_wait,
            "reason": (
                "adaptive_pressure_pocket_suspension"
                if app_suspension_seconds > 0
                else "feature_pressure"
                if event_delay > 0
                else "provider_baseline_spacing"
                if baseline_wait > 0
                else "baseline"
            ),
            "profile_sequence": int(session.profile.get("sequence", 0)),
            "metadata": {
                **dict(workload.metadata),
                "app_eligible": app_eligible,
                "app_suspension": {
                    "reason": app_suspension_reason,
                    "duration_seconds": app_suspension_seconds,
                    "metadata": app_suspension_metadata,
                } if app_eligible else None,
            },
        }

        session.decisions.append(dict(record))
        self._report_action_admission(
            session,
            context,
            rendered_action,
            workload,
            record,
        )
        session.store.persist_profile(session.profile)
        return provider_pressure_action_admission(
            context=context,
            action_id=rendered_action,
            workload=workload,
            record=MappingProxyType(record),
        )

    def pacing_observe(
        self,
        session: provider_pressure_execution_session,
        context: runtime_provider_pressure_run_context,
        event: runtime_provider_pressure_pacing_event,
    ) -> runtime_provider_pressure_observation:
        self._record_pacing_event(session, event)
        if event.source != "runtime":
            return runtime_provider_pressure_observation(
                source_kind="live_execution",
                source_execution_id=context.execution_id,
                signals=(),
                metadata={"ignored_pacing_source": event.source},
            )
        observation = session.model.pacing_observe(context, event)
        self._apply_observation(session, observation)
        session.profile["updated_at"] = _iso(self._now())
        session.store.persist_profile(session.profile)
        return observation

    def recovery_actuate(
        self,
        session: provider_pressure_execution_session,
        context: runtime_provider_pressure_run_context,
        event: runtime_provider_pressure_recovery_event,
    ) -> provider_pressure_recovery_admission:
        action_record = _find_action_decision(
            session.decisions,
            decision_id=event.decision_id,
            action_id=event.action_id,
        )
        event_pph_wait = (
            float(action_record.get("event_pph_wait_seconds", 0.0) or 0.0)
            if action_record is not None
            else 0.0
        )
        app_suspension_wait = (
            float(action_record.get("app_suspension_seconds", 0.0) or 0.0)
            if action_record is not None
            else 0.0
        )
        total_admission_wait = event_pph_wait + app_suspension_wait
        underprediction = max(0.0, float(event.wait_seconds) - event_pph_wait)
        admission_decision = (
            {
                "decision_id": action_record.get("decision_id"),
                "action_id": action_record.get("action_id"),
                "effective_pressure": action_record.get("event_pph_pressure"),
                "event_pph_pressure": action_record.get("event_pph_pressure"),
                "event_pph_wait_seconds": action_record.get("event_pph_wait_seconds"),
                "app_suspension_seconds": action_record.get("app_suspension_seconds"),
                "total_admission_wait_seconds": action_record.get("total_admission_wait_seconds"),
                "recommended_wait_seconds": action_record.get("recommended_wait_seconds"),
                "metadata": dict(action_record.get("metadata", {})),
            }
            if action_record is not None
            else {}
        )
        enriched = replace(
            event,
            decision_id=(
                str(action_record.get("decision_id"))
                if action_record is not None and action_record.get("decision_id")
                else event.decision_id
            ),
            admission_wait_seconds=event_pph_wait,
            underprediction_seconds=underprediction,
            metadata={
                **dict(event.metadata),
                "admission_decision": admission_decision,
            },
        )

        recovery_observe = getattr(session.model, "recovery_observe", None)
        if callable(recovery_observe):
            observation = recovery_observe(context, enriched)
        else:
            observation = runtime_provider_pressure_observation(
                source_kind="live_execution",
                source_execution_id=context.execution_id,
                signals=(),
                metadata={
                    "compatibility": "model_without_recovery_observe",
                    "provider_recovery_kind": enriched.kind,
                },
            )
        self._apply_observation(session, observation)

        model_state_metadata = self._app_recovery_observe(
            session,
            context,
            enriched,
            action_record=action_record,
        )

        pacing = runtime_provider_pressure_pacing_event(
            source="pph",
            reason="provider_directed_recovery",
            duration_seconds=float(enriched.wait_seconds),
            action_id=enriched.action_id,
            features=enriched.features,
            metadata={
                "run_index": context.run_index,
                "scenario_id": context.scenario_id,
                "subject_id": context.subject_id,
                "provider_recovery_kind": enriched.kind,
                "recommendation_source": enriched.recommendation_source,
                "decision_id": enriched.decision_id,
                "event_pph_wait_seconds": event_pph_wait,
                "app_suspension_seconds": app_suspension_wait,
                "total_admission_wait_seconds": total_admission_wait,
                "underprediction_seconds": underprediction,
                **dict(enriched.metadata),
            },
        )
        self._record_pacing_event(session, pacing)

        record = {
            "decision_kind": "recovery",
            "run_index": context.run_index,
            "scenario_id": context.scenario_id,
            "subject_id": context.subject_id,
            "action_id": enriched.action_id,
            "decision_id": enriched.decision_id,
            "provider_recovery_kind": enriched.kind,
            "recommendation_source": enriched.recommendation_source,
            "provider_wait_seconds": float(enriched.wait_seconds),
            "admission_wait_seconds": event_pph_wait,
            "event_pph_wait_seconds": event_pph_wait,
            "app_suspension_seconds": app_suspension_wait,
            "total_admission_wait_seconds": total_admission_wait,
            "underprediction_seconds": underprediction,
            "features": dict(enriched.features),
            "signal_count": len(observation.signals),
            "model_state_metadata": dict(model_state_metadata),
            "metadata": dict(enriched.metadata),
        }
        session.decisions.append(dict(record))
        session.profile["updated_at"] = _iso(self._now())
        session.store.persist_profile(session.profile)

        if context.progress is not None and enriched.wait_seconds > 0:
            source = str(enriched.recommendation_source).replace("_", "-")
            message = (
                "PPH: "
                + enriched.kind
                + " recovery for "
                + enriched.action_id
                + " from "
                + source
                + ", idling ~"
                + _approximate_duration(enriched.wait_seconds)
            )
            if underprediction > 0:
                message += (
                    "; Event PPH missed by ~"
                    + _approximate_duration(underprediction)
                    + " (predicted ~"
                    + _approximate_duration(event_pph_wait)
                    + ")"
                )
            context.progress(message + ".")
        if enriched.wait_seconds > 0:
            self._sleep(float(enriched.wait_seconds))

        return provider_pressure_recovery_admission(
            context=context,
            event=enriched,
            record=MappingProxyType(record),
        )

    def action_result_observe(
        self,
        session: provider_pressure_execution_session,
        context: runtime_provider_pressure_run_context,
        event: runtime_provider_pressure_action_result_event,
    ) -> Mapping[str, Any]:
        action_record = _find_action_decision(
            session.decisions,
            decision_id=event.decision_id,
            action_id=event.action_id,
        )
        decision_id = (
            str(action_record.get("decision_id"))
            if action_record is not None and action_record.get("decision_id")
            else event.decision_id
        )
        admission_decision = (
            {
                "decision_id": action_record.get("decision_id"),
                "action_id": action_record.get("action_id"),
                "effective_pressure": action_record.get("event_pph_pressure"),
                "event_pph_pressure": action_record.get("event_pph_pressure"),
                "event_pph_wait_seconds": action_record.get("event_pph_wait_seconds"),
                "app_suspension_seconds": action_record.get("app_suspension_seconds"),
                "total_admission_wait_seconds": action_record.get("total_admission_wait_seconds"),
                "recommended_wait_seconds": action_record.get("recommended_wait_seconds"),
                "metadata": dict(action_record.get("metadata", {})),
            }
            if action_record is not None
            else {}
        )
        enriched = replace(
            event,
            decision_id=decision_id,
            metadata={
                **dict(event.metadata),
                "admission_decision": admission_decision,
            },
        )

        model_state_metadata = self._app_action_result_observe(
            session,
            context,
            enriched,
        )

        record = {
            "decision_kind": "action_result",
            "run_index": context.run_index,
            "scenario_id": context.scenario_id,
            "subject_id": context.subject_id,
            "action_id": enriched.action_id,
            "decision_id": enriched.decision_id,
            "outcome": enriched.outcome,
            "features": dict(enriched.features),
            "model_state_metadata": dict(model_state_metadata),
            "metadata": dict(enriched.metadata),
        }
        session.decisions.append(dict(record))
        session.profile["updated_at"] = _iso(self._now())
        session.store.persist_profile(session.profile)
        return MappingProxyType(record)

    def _app_recovery_observe(
        self,
        session: provider_pressure_execution_session,
        context: runtime_provider_pressure_run_context,
        event: runtime_provider_pressure_recovery_event,
        *,
        action_record: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        policy = _runtime_app_policy(session)
        if not policy.eligible(event.action_id):
            return {"adaptive_pressure_pocket": "not_eligible"}
        if action_record is None:
            return {"adaptive_pressure_pocket": "missing_admission"}
        admission_metadata = action_record.get("metadata")
        app_suspension = (
            admission_metadata.get("app_suspension")
            if isinstance(admission_metadata, Mapping)
            else None
        )
        app_component = (
            app_suspension.get("metadata")
            if isinstance(app_suspension, Mapping)
            else None
        )
        if not isinstance(app_component, Mapping):
            return {"adaptive_pressure_pocket": "missing_suspension_metadata"}

        model_state = session.profile.setdefault("model_state", {})
        core_state = _core_app_state_mutable(model_state)
        decision_key = str(
            event.decision_id
            or f"{context.execution_id}:run={context.run_index}:{event.action_id}"
        )
        probe = core_state["open_probes"].get(decision_key)
        if not isinstance(probe, dict):
            sequence = int(core_state.get("next_probe_sequence", 0))
            core_state["next_probe_sequence"] = sequence + 1
            probe = {
                "probe_id": (
                    f"{context.execution_id}:run={context.run_index}:app={sequence:04d}"
                ),
                "decision_id": event.decision_id,
                "execution_id": context.execution_id,
                "run_index": context.run_index,
                "subject_id": context.subject_id,
                "scenario_id": context.scenario_id,
                "action_id": event.action_id,
                "features": dict(event.features),
                "event_pph_pressure": float(
                    action_record.get("event_pph_pressure", 0.0) or 0.0
                ),
                "event_pph_wait_seconds": float(
                    action_record.get("event_pph_wait_seconds", 0.0) or 0.0
                ),
                "app_suspension_seconds": float(
                    action_record.get("app_suspension_seconds", 0.0) or 0.0
                ),
                "total_admission_wait_seconds": float(
                    action_record.get("total_admission_wait_seconds", 0.0) or 0.0
                ),
                "component_metadata": dict(app_component),
                "opened_at": _iso(self._now()),
                "recovery_events": [],
                "total_provider_recovery_seconds": 0.0,
                "total_underprediction_seconds": 0.0,
                "status": "open",
            }
            core_state["open_probes"][decision_key] = probe

        hook_metadata: Mapping[str, Any] = {}
        hook = getattr(session.model, "app_probe_recovery_metadata", None)
        if callable(hook):
            hook_metadata = hook(
                context,
                event,
                MappingProxyType(_copy_mapping(probe)),
                MappingProxyType(_copy_mapping(model_state)),
            ) or {}
            if not isinstance(hook_metadata, Mapping):
                raise TypeError(
                    "Provider Pressure app_probe_recovery_metadata() must return a mapping or None."
                )
        recovery_events = probe.setdefault("recovery_events", [])
        recovery_events.append(
            {
                "ordinal": len(recovery_events) + 1,
                "kind": event.kind,
                "recommendation_source": event.recommendation_source,
                "provider_wait_seconds": float(event.wait_seconds),
                "underprediction_seconds": float(event.underprediction_seconds),
                "observed_at": _iso(self._now()),
                "metadata": dict(hook_metadata),
            }
        )
        probe["total_provider_recovery_seconds"] = float(
            probe.get("total_provider_recovery_seconds", 0.0)
        ) + float(event.wait_seconds)
        probe["total_underprediction_seconds"] = float(
            probe.get("total_underprediction_seconds", 0.0)
        ) + float(event.underprediction_seconds)
        probe["last_recovery_at"] = _iso(self._now())
        return {
            "app_probe_id": probe["probe_id"],
            "app_probe_state": "open",
            "app_probe_recovery_count": len(recovery_events),
        }

    def _app_action_result_observe(
        self,
        session: provider_pressure_execution_session,
        context: runtime_provider_pressure_run_context,
        event: runtime_provider_pressure_action_result_event,
    ) -> Mapping[str, Any]:
        policy = _runtime_app_policy(session)
        if not policy.eligible(event.action_id):
            return {"adaptive_pressure_pocket": "not_eligible"}
        model_state = session.profile.setdefault("model_state", {})
        core_state = _core_app_state_mutable(model_state)
        decision_key = str(
            event.decision_id
            or f"{context.execution_id}:run={context.run_index}:{event.action_id}"
        )
        probe = core_state["open_probes"].pop(decision_key, None)
        if not isinstance(probe, dict):
            hook = getattr(session.model, "app_safe_observe", None)
            if not callable(hook):
                return {"adaptive_pressure_pocket": "no_open_probe"}
            result = hook(context, event, model_state) or {}
            if not isinstance(result, Mapping):
                raise TypeError(
                    "Provider Pressure app_safe_observe() must return a mapping or None."
                )
            return dict(result)

        probe["status"] = "closed"
        probe["resolution"] = event.outcome
        probe["closed_at"] = _iso(self._now())
        probe["result_metadata"] = dict(event.metadata)
        hook = getattr(session.model, "app_probe_close", None)
        hook_result: Mapping[str, Any] = {}
        if callable(hook):
            hook_result = hook(
                context,
                event,
                MappingProxyType(_copy_mapping(probe)),
                model_state,
            ) or {}
            if not isinstance(hook_result, Mapping):
                raise TypeError(
                    "Provider Pressure app_probe_close() must return a mapping or None."
                )
        recent = core_state["recent_probes"]
        recent.append(_copy_mapping(probe))
        if len(recent) > policy.max_recent_probes:
            del recent[:-policy.max_recent_probes]
        return {
            "app_probe_id": probe["probe_id"],
            "app_probe_state": "closed",
            "app_probe_resolution": event.outcome,
            "app_probe_recovery_count": len(probe.get("recovery_events", ())),
            **dict(hook_result),
        }

    def _record_pacing_event(
        self,
        session: provider_pressure_execution_session,
        event: runtime_provider_pressure_pacing_event,
    ) -> None:
        record = {
            "source": event.source,
            "reason": event.reason,
            "duration_seconds": float(event.duration_seconds),
            "action_id": event.action_id,
            "features": dict(event.features),
            "metadata": dict(event.metadata),
        }
        session.pacing_events.append(record)
        if event.source == "pph" and event.duration_seconds > 0:
            totals = session.profile.setdefault("totals", {})
            totals["pph_pacing_events"] = int(totals.get("pph_pacing_events", 0)) + 1
            totals["pph_pacing_seconds"] = float(totals.get("pph_pacing_seconds", 0.0)) + float(event.duration_seconds)
            states = session.profile.setdefault("features", {})
            for feature, weight in event.features.items():
                state = _feature_state(states, feature, session.policy)
                state["pph_actuation_events"] = int(state.get("pph_actuation_events", 0)) + 1
                state["pph_actuation_seconds"] = float(state.get("pph_actuation_seconds", 0.0)) + float(event.duration_seconds) * float(weight)
            session.profile["updated_at"] = _iso(self._now())

    def _report_action_admission(
        self,
        session: provider_pressure_execution_session,
        context: runtime_provider_pressure_run_context,
        action_id: str,
        workload: runtime_provider_pressure_workload,
        record: Mapping[str, Any],
    ) -> None:
        if context.progress is None:
            return
        selector = getattr(session.model, "action_console_report", None)
        if not callable(selector):
            return
        if not bool(
            selector(
                context,
                action_id,
                workload,
                MappingProxyType(dict(record)),
            )
        ):
            return

        metadata = workload.metadata
        method = str(metadata.get("transport_method") or "").strip().upper()
        path = str(metadata.get("transport_path") or "").strip()
        plane = str(metadata.get("transport_plane") or "").strip()
        details = " ".join(value for value in (plane, method, path) if value)
        total_wait = max(
            0.0,
            float(record.get("total_admission_wait_seconds", 0.0) or 0.0),
        )
        message = (
            "PPH: "
            + action_id
            + " admitted; wait ~"
            + _approximate_duration(total_wait)
            + " ("
            + str(record.get("reason") or "baseline").replace("_", "-")
            + ")"
        )
        if total_wait > 0:
            message += (
                " app="
                + _approximate_duration(float(record.get("app_suspension_seconds", 0.0) or 0.0))
                + " learned="
                + _approximate_duration(float(record.get("event_pph_wait_seconds", 0.0) or 0.0))
                + " baseline="
                + _approximate_duration(float(record.get("baseline_spacing_wait_seconds", 0.0) or 0.0))
            )
        spacing_target = max(
            0.0,
            float(record.get("baseline_spacing_target_seconds", 0.0) or 0.0),
        )
        if spacing_target > 0:
            elapsed_value = record.get("baseline_spacing_elapsed_seconds")
            elapsed_text = (
                "n/a"
                if elapsed_value is None
                else _approximate_duration(max(0.0, float(elapsed_value)))
            )
            message += (
                " spacing target="
                + _approximate_duration(spacing_target)
                + " elapsed="
                + elapsed_text
                + " remaining="
                + _approximate_duration(
                    float(record.get("baseline_spacing_wait_seconds", 0.0) or 0.0)
                )
            )
        if details:
            message += " [" + details + "]"
        decision_id = str(record.get("decision_id") or "").strip()
        if decision_id:
            message += " decision=" + decision_id
        context.progress(message + ".")

    def checkpoint_admit(
        self,
        session: provider_pressure_execution_session,
        context: runtime_provider_pressure_run_context,
        *,
        checkpoint_id: str,
        workload: runtime_provider_pressure_workload,
    ) -> provider_pressure_action_admission:
        """Backward-compatible alias for the retired checkpoint terminology."""
        return self.action_admit(
            session,
            context,
            action_id=checkpoint_id,
            workload=workload,
        )

    def run_finalize(
        self,
        session: provider_pressure_execution_session,
        admission: provider_pressure_run_admission,
        outcome: runtime_provider_pressure_run_outcome,
    ) -> runtime_provider_pressure_observation:
        observation = session.model.run_observe(
            admission.context,
            admission.measurement_handle,
            outcome,
        )
        self._apply_observation(session, observation)
        session.profile["updated_at"] = _iso(self._now())
        return observation

    def execution_finalize(
        self,
        session: provider_pressure_execution_session,
        *,
        status: str,
    ) -> Path:
        now = self._now()
        session.profile["updated_at"] = _iso(now)
        session.profile["sequence"] = int(session.profile.get("sequence", 0)) + 1
        session.store.persist_profile(session.profile)
        source_key = f"live_execution:{session.context.execution_id}"
        if source_key not in session.profile.get("applied_sources", ()):
            session.profile["applied_sources"] = [
                *list(session.profile.get("applied_sources", ())),
                source_key,
            ]
            session.store.persist_profile(session.profile)
        return session.store.append_snapshot(
            profile_before=session.profile_before,
            profile_after=session.profile,
            identity=session.identity,
            source_kind="live_execution",
            source_execution_id=session.context.execution_id,
            execution_status=status,
            applied_signals=tuple(session.execution_signals),
            decisions=tuple(session.decisions),
            source_metadata={
                "experiment_id": session.context.experiment_id,
                "package_sha256": session.context.package_sha256,
                "selected_run_indices": list(session.context.selected_run_indices),
                "session_dir": session.context.session_dir,
                "pacing_events": list(session.pacing_events),
            },
            created_at=now,
        )

    def _apply_observation(
        self,
        session: provider_pressure_execution_session,
        observation: runtime_provider_pressure_observation,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for signal in observation.signals:
            record = _apply_signal(session.profile, signal, session.policy)
            record["source_kind"] = observation.source_kind
            record["source_execution_id"] = observation.source_execution_id
            if observation.metadata:
                record["observation_metadata"] = dict(observation.metadata)
            records.append(record)
            if observation.source_kind == "live_execution":
                session.execution_signals.append(dict(record))
        return records


def _validate_document(schema_name: str, document: Mapping[str, Any]) -> None:
    schema_path = Path(__file__).with_name("schemas") / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(dict(document)), key=lambda item: list(item.path))
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(part) for part in error.path) or "<root>"
    raise ValueError(
        f"Provider Pressure schema violation at {location}: {error.message}"
    )


def _new_profile(
    identity: runtime_provider_pressure_identity,
    policy: runtime_provider_pressure_policy,
    now: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": PROVIDER_PRESSURE_PROFILE_SCHEMA_VERSION,
        "identity": _identity_record(identity),
        "sequence": 0,
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "features": {},
        "model_state": {},
        "totals": {
            "observations": 0,
            "reward_events": 0,
            "punishment_events": 0,
            "runtime_pressure_events": 0,
            "runtime_pressure_seconds": 0.0,
            "pph_pacing_events": 0,
            "pph_pacing_seconds": 0.0,
        },
        "applied_sources": [],
    }


def _feature_state(
    feature_states: dict[str, Any],
    feature: str,
    policy: runtime_provider_pressure_policy,
) -> dict[str, Any]:
    return feature_states.setdefault(
        feature,
        {
            "score": policy.baseline_score,
            "observations": 0,
            "rewards": 0,
            "punishments": 0,
            "confidence": 0.0,
            "runtime_pressure_events": 0,
            "runtime_pressure_seconds": 0.0,
            "pph_actuation_events": 0,
            "pph_actuation_seconds": 0.0,
        },
    )


def _apply_signal(
    profile: dict[str, Any],
    signal: runtime_provider_pressure_signal,
    policy: runtime_provider_pressure_policy,
) -> dict[str, Any]:
    totals = profile.setdefault("totals", {})
    feature_states = profile.setdefault("features", {})
    if signal.kind == "punishment":
        totals["punishment_events"] = int(totals.get("punishment_events", 0)) + signal.count
        if signal.pressure_duration_seconds > 0:
            totals["runtime_pressure_events"] = int(totals.get("runtime_pressure_events", 0)) + signal.count
            totals["runtime_pressure_seconds"] = float(totals.get("runtime_pressure_seconds", 0.0)) + float(signal.pressure_duration_seconds)
    else:
        totals["reward_events"] = int(totals.get("reward_events", 0)) + signal.count
    totals["observations"] = int(totals.get("observations", 0)) + signal.count

    feature_changes: dict[str, dict[str, Any]] = {}
    for feature, weight in signal.features.items():
        state = _feature_state(feature_states, feature, policy)
        before = float(state.get("score", policy.baseline_score))
        if signal.kind == "punishment":
            delta = (
                policy.punishment_increment
                * signal.severity
                * signal.count
                * float(weight)
            )
            after = min(policy.score_ceiling, before + delta)
            state["punishments"] = int(state.get("punishments", 0)) + signal.count
            if signal.pressure_duration_seconds > 0:
                state["runtime_pressure_events"] = int(state.get("runtime_pressure_events", 0)) + signal.count
                state["runtime_pressure_seconds"] = float(state.get("runtime_pressure_seconds", 0.0)) + float(signal.pressure_duration_seconds) * float(weight)
        else:
            delta = (
                policy.reward_decrement
                * signal.severity
                * signal.count
                * float(weight)
            )
            after = max(policy.baseline_score, before - delta)
            state["rewards"] = int(state.get("rewards", 0)) + signal.count
        state["score"] = after
        state["observations"] = int(state.get("observations", 0)) + signal.count
        state["confidence"] = _confidence(
            int(state["observations"]),
            policy.confidence_observation_scale,
        )
        feature_changes[feature] = {
            "weight": float(weight),
            "score_before": before,
            "score_after": after,
            "runtime_pressure_seconds": float(signal.pressure_duration_seconds) * float(weight),
        }

    return {
        "kind": signal.kind,
        "count": signal.count,
        "severity": signal.severity,
        "pressure_duration_seconds": signal.pressure_duration_seconds,
        "features": feature_changes,
        "metadata": dict(signal.metadata),
    }


def _find_action_decision(
    decisions: list[dict[str, Any]],
    *,
    decision_id: str | None,
    action_id: str,
) -> Mapping[str, Any] | None:
    if decision_id is not None:
        for item in reversed(decisions):
            if (
                item.get("decision_kind") == "action"
                and item.get("decision_id") == decision_id
            ):
                return item
    for item in reversed(decisions):
        if (
            item.get("decision_kind") == "action"
            and item.get("action_id") == action_id
        ):
            return item
    return None


def _workload_pressure(
    profile: Mapping[str, Any],
    workload: runtime_provider_pressure_workload,
    policy: runtime_provider_pressure_policy,
) -> float:
    pressure = policy.baseline_score
    feature_states = profile.get("features") if isinstance(profile.get("features"), Mapping) else {}
    for feature, weight in workload.features.items():
        state = feature_states.get(feature)
        score = (
            float(state.get("score", policy.baseline_score))
            if isinstance(state, Mapping)
            else policy.baseline_score
        )
        weighted = policy.baseline_score + (
            score - policy.baseline_score
        ) * min(1.0, max(0.0, float(weight)))
        pressure = max(pressure, weighted)
    return min(policy.score_ceiling, max(policy.baseline_score, pressure))


def _upgrade_profile_document(profile: Mapping[str, Any]) -> dict[str, Any]:
    value = _copy_mapping(profile)
    if value.get("schema_version") == "0.4":
        value["schema_version"] = PROVIDER_PRESSURE_PROFILE_SCHEMA_VERSION
        value.setdefault("model_state", {})
    return value


def _profile_identity_matches(
    profile: Mapping[str, Any],
    identity: runtime_provider_pressure_identity,
) -> bool:
    stored = profile.get("identity")
    return isinstance(stored, Mapping) and dict(stored) == _identity_record(identity)


def _identity_record(identity: runtime_provider_pressure_identity) -> dict[str, str]:
    return {
        "provider_id": identity.provider_id,
        "scope_id": identity.scope_id,
        "model_id": identity.model_id,
        "model_version": identity.model_version,
        "model_definition_sha256": identity.model_definition_sha256,
    }


def _profile_projection(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sequence": int(profile.get("sequence", 0)),
        "updated_at": profile.get("updated_at"),
        "features": _copy_mapping(profile.get("features", {})),
        "model_state": _copy_mapping(profile.get("model_state", {})),
        "totals": _copy_mapping(profile.get("totals", {})),
    }


def _copy_mapping(value: Any) -> dict[str, Any]:
    return json.loads(json.dumps(dict(value) if isinstance(value, Mapping) else {}))


def _confidence(observations: int, scale: int) -> float:
    return round(1.0 - math.exp(-max(0, observations) / float(scale)), 6)


def _document_sha256(document: Mapping[str, Any]) -> str:
    payload = yaml.safe_dump(
        dict(document),
        sort_keys=True,
        allow_unicode=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_yaml_atomically(path: Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    rendered = yaml.safe_dump(
        dict(value),
        sort_keys=False,
        allow_unicode=False,
    )
    try:
        temporary.write_text(rendered, encoding="utf-8")
        os.replace(temporary, target)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _safe_component(value: str) -> str:
    rendered = _SAFE_COMPONENT.sub("-", str(value).strip()).strip("-.")
    return rendered or "unknown"


def _iso(value: datetime) -> str:
    current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_timestamp(value: datetime) -> str:
    current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _approximate_duration(seconds: float) -> str:
    whole_seconds = max(0, int(round(float(seconds))))
    rendered_seconds = f"{whole_seconds}s"
    if whole_seconds < 60:
        return rendered_seconds
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds_part:
        parts.append(f"{seconds_part}s")
    return f"{rendered_seconds} ({''.join(parts)})"


provider_pressure_checkpoint_admission = provider_pressure_action_admission


__all__ = [
    "PROVIDER_PRESSURE_INDEX_SCHEMA_VERSION",
    "PROVIDER_PRESSURE_PROFILE_SCHEMA_VERSION",
    "PROVIDER_PRESSURE_ROOT_NAME",
    "PROVIDER_PRESSURE_TRAJECTORY_SCHEMA_VERSION",
    "provider_pressure_action_admission",
    "provider_pressure_checkpoint_admission",
    "provider_pressure_engine",
    "provider_pressure_recovery_admission",
    "provider_pressure_execution_session",
    "provider_pressure_run_admission",
    "provider_pressure_store",
    "runtime_provider_pressure_action_result_event",
    "runtime_provider_pressure_app_suspension",
    "runtime_provider_pressure_app_policy",
    "runtime_provider_pressure_execution_context",
    "runtime_provider_pressure_identity",
    "runtime_provider_pressure_model",
    "runtime_provider_pressure_observation",
    "runtime_provider_pressure_pacing_event",
    "runtime_provider_pressure_policy",
    "runtime_provider_pressure_recovery_event",
    "runtime_provider_pressure_run_context",
    "runtime_provider_pressure_run_outcome",
    "runtime_provider_pressure_signal",
    "runtime_provider_pressure_gate_configuration",
    "runtime_provider_pressure_transport_action",
    "runtime_provider_pressure_transport_recovery",
    "runtime_provider_pressure_transport_request",
    "runtime_provider_pressure_transport_response",
    "runtime_provider_pressure_workload",
]
