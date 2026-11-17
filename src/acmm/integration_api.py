from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from acmm.secret_bindings import has_secret_binding, resolve_secret, secret_binding_key


EXPERIMENT_INTEGRATION_API_VERSION = "0.2"


@dataclass(frozen=True)
class integration_check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class integration_report:
    checks: tuple[integration_check, ...]

    @property
    def is_ready(self) -> bool:
        return all(item.ok for item in self.checks)


@dataclass(frozen=True)
class experiment_integration_context:
    experiment_id: str
    integration_id: str
    preset: str | None
    configuration: Mapping[str, Any]
    package_sha256: str
    package_root: Path
    secret_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration", MappingProxyType(dict(self.configuration)))
        object.__setattr__(self, "secret_ids", tuple(self.secret_ids))

    def resolve_resource(self, relative_path: str | Path) -> Path:
        value = str(relative_path)
        relative = Path(relative_path)
        if (
            not value.strip()
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("Integration resource path must be package-relative and safe.")

        package_root = self.package_root.resolve()
        source_resource = package_root / relative
        _reject_resource_symlinks(source_resource, package_root)
        resource = source_resource.resolve()
        try:
            resource.relative_to(package_root)
        except ValueError as exc:
            raise ValueError(
                "Integration resource path resolves outside the package root."
            ) from exc
        if not resource.exists():
            raise ValueError(f"Integration resource does not exist: '{relative}'.")
        return resource

    def has_secret(self, secret_id: str) -> bool:
        self._require_secret_id(secret_id)
        return has_secret_binding(self._secret_key(secret_id))

    def resolve_secret(self, secret_id: str) -> str:
        self._require_secret_id(secret_id)
        return resolve_secret(self._secret_key(secret_id))

    def _secret_key(self, secret_id: str) -> secret_binding_key:
        return secret_binding_key(
            experiment_id=self.experiment_id,
            integration_id=self.integration_id,
            secret_id=secret_id,
        )

    def _require_secret_id(self, secret_id: str) -> None:
        if secret_id not in self.secret_ids:
            raise ValueError(
                f"Integration '{self.integration_id}' did not declare secret "
                f"'{secret_id}'."
            )


@dataclass(frozen=True)
class subject_integration_context:
    experiment_id: str
    integration_id: str
    preset: str | None
    configuration: Mapping[str, Any]
    package_sha256: str
    package_root: Path
    infrastructure_subject: str
    llm_baseline_id: str
    secret_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration", MappingProxyType(dict(self.configuration)))
        object.__setattr__(self, "secret_ids", tuple(self.secret_ids))

    def _experiment_context(self) -> experiment_integration_context:
        return experiment_integration_context(
            experiment_id=self.experiment_id,
            integration_id=self.integration_id,
            preset=self.preset,
            configuration=self.configuration,
            package_sha256=self.package_sha256,
            package_root=self.package_root,
            secret_ids=self.secret_ids,
        )

    def resolve_resource(self, relative_path: str | Path) -> Path:
        return self._experiment_context().resolve_resource(relative_path)

    def has_secret(self, secret_id: str) -> bool:
        return self._experiment_context().has_secret(secret_id)

    def resolve_secret(self, secret_id: str) -> str:
        return self._experiment_context().resolve_secret(secret_id)


@dataclass(frozen=True)
class shared_integration_context:
    experiment_id: str
    integration_id: str
    preset: str | None
    configuration: Mapping[str, Any]
    package_sha256: str
    package_root: Path
    runtime_root: Path
    state_root: Path
    secret_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration", MappingProxyType(dict(self.configuration)))
        object.__setattr__(self, "secret_ids", tuple(self.secret_ids))

    def resolve_secret(self, secret_id: str) -> str:
        context = experiment_integration_context(
            experiment_id=self.experiment_id,
            integration_id=self.integration_id,
            preset=self.preset,
            configuration=self.configuration,
            package_sha256=self.package_sha256,
            package_root=self.package_root,
            secret_ids=self.secret_ids,
        )
        return context.resolve_secret(secret_id)


@dataclass(frozen=True)
class tool_result_interpretation_context:
    subject_id: str
    tool_name: str
    output: Any


class tool_result_error_interpreter(Protocol):
    def detects_error(
        self,
        context: tool_result_interpretation_context,
    ) -> bool:
        ...

    # Optional at runtime. Implementations may attach namespaced interpretation
    # metadata to normalized failed tool results. Core preserves the annotations
    # but does not interpret integration- or provider-specific values.
    def annotations(
        self,
        context: tool_result_interpretation_context,
    ) -> Mapping[str, Any]:
        ...


class subject_profile_integration(Protocol):
    def compose_profile(
        self,
        context: subject_integration_context,
        profile: object,
    ) -> object:
        ...


class subject_manager_integration(Protocol):
    def compose_manager(
        self,
        context: subject_integration_context,
        manager: object,
    ) -> object:
        ...




class subject_runtime_admission_integration(Protocol):
    """Bind and revoke run-scoped subject authority.

    ``admit()`` must be failure-atomic for its own side effects. Core can unwind
    handles returned by earlier integrations, but it cannot revoke an admission
    that raises before returning its handle.
    """

    def admit(
        self,
        context: subject_integration_context,
        manager: object,
        payload: Mapping[str, Any],
    ) -> Any:
        ...

    def revoke(
        self,
        context: subject_integration_context,
        manager: object,
        handle: Any,
    ) -> Mapping[str, Any] | None:
        ...


class shared_resource_integration(Protocol):
    def apply(
        self,
        context: shared_integration_context,
        *,
        timeout_seconds: float = 120.0,
        progress=None,
    ) -> Mapping[str, Any]:
        ...

    def status(self, context: shared_integration_context) -> Mapping[str, Any]:
        ...

    def is_running(self, context: shared_integration_context) -> bool:
        ...

    def up(
        self,
        context: shared_integration_context,
        *,
        timeout_seconds: float = 120.0,
        progress=None,
    ) -> None:
        ...

    def doctor(self, context: shared_integration_context) -> integration_report:
        ...

    def down(self, context: shared_integration_context) -> None:
        ...

    def remove(self, context: shared_integration_context) -> None:
        ...


@dataclass(frozen=True)
class subject_integration:
    profile: subject_profile_integration | None = None
    manager: subject_manager_integration | None = None
    admission: subject_runtime_admission_integration | None = None

    def __post_init__(self) -> None:
        if self.profile is None and self.manager is None and self.admission is None:
            raise ValueError(
                "Subject integration must provide profile, manager, and/or runtime "
                "admission behavior."
            )


@dataclass(frozen=True)
class experiment_integration:
    subjects: Mapping[str, subject_integration] = field(default_factory=dict)
    shared_resource: shared_resource_integration | None = None
    tool_result_error_interpreters: tuple[tool_result_error_interpreter, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "subjects", MappingProxyType(dict(self.subjects)))
        object.__setattr__(
            self,
            "tool_result_error_interpreters",
            tuple(self.tool_result_error_interpreters),
        )
        if (
            not self.subjects
            and self.shared_resource is None
            and not self.tool_result_error_interpreters
        ):
            raise ValueError(
                "Experiment integration must provide subjects, a shared resource, "
                "and/or tool-result interpretation."
            )


def _reject_resource_symlinks(resource: Path, package_root: Path) -> None:
    current = resource
    while current != package_root:
        if current.is_symlink():
            raise ValueError("Integration resource path must not traverse symbolic links.")
        current = current.parent


__all__ = [
    "EXPERIMENT_INTEGRATION_API_VERSION",
    "experiment_integration",
    "experiment_integration_context",
    "integration_check",
    "integration_report",
    "shared_integration_context",
    "shared_resource_integration",
    "subject_integration",
    "subject_integration_context",
    "subject_manager_integration",
    "subject_runtime_admission_integration",
    "tool_result_error_interpreter",
    "tool_result_interpretation_context",
    "subject_profile_integration",
]
