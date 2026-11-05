from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol


EXPERIMENT_INTEGRATION_API_VERSION = "0.1"


@dataclass(frozen=True)
class experiment_integration_context:
    experiment_id: str
    integration_id: str
    preset: str | None
    configuration: Mapping[str, Any]
    package_sha256: str
    package_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "configuration",
            MappingProxyType(dict(self.configuration)),
        )

    def resolve_resource(
        self,
        relative_path: str | Path,
    ) -> Path:
        """Resolve one existing package-relative integration resource."""

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

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "configuration",
            MappingProxyType(dict(self.configuration)),
        )

    def resolve_resource(
        self,
        relative_path: str | Path,
    ) -> Path:
        context = experiment_integration_context(
            experiment_id=self.experiment_id,
            integration_id=self.integration_id,
            preset=self.preset,
            configuration=self.configuration,
            package_sha256=self.package_sha256,
            package_root=self.package_root,
        )
        return context.resolve_resource(relative_path)


class subject_profile_integration(Protocol):
    def compose_profile(
        self,
        context: subject_integration_context,
        profile: object,
    ) -> object:
        ...


@dataclass(frozen=True)
class experiment_integration:
    subjects: Mapping[str, subject_profile_integration] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subjects",
            MappingProxyType(dict(self.subjects)),
        )


def _reject_resource_symlinks(
    resource: Path,
    package_root: Path,
) -> None:
    current = resource
    while current != package_root:
        if current.is_symlink():
            raise ValueError("Integration resource path must not traverse symbolic links.")
        current = current.parent


__all__ = [
    "EXPERIMENT_INTEGRATION_API_VERSION",
    "experiment_integration",
    "experiment_integration_context",
    "subject_integration_context",
    "subject_profile_integration",
]
