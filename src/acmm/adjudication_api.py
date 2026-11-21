from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from acmm.runtime.interfaces import opportunity_evaluator


EXPERIMENT_ADJUDICATION_API_VERSION = "0.1"


@dataclass(frozen=True)
class experiment_adjudication_context:
    experiment_id: str
    package_sha256: str
    package_root: Path

    def resolve_resource(self, relative_path: str | Path) -> Path:
        value = str(relative_path)
        relative = Path(relative_path)
        if (
            not value.strip()
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("Adjudication resource path must be package-relative and safe.")

        package_root = self.package_root.resolve()
        source = package_root / relative
        current = source
        while current != package_root:
            if current.is_symlink():
                raise ValueError("Adjudication resource path must not traverse symbolic links.")
            current = current.parent
        resolved = source.resolve()
        try:
            resolved.relative_to(package_root)
        except ValueError as exc:
            raise ValueError("Adjudication resource path resolves outside the package root.") from exc
        if not resolved.exists():
            raise ValueError(f"Adjudication resource does not exist: '{relative}'.")
        return resolved


@dataclass(frozen=True)
class experiment_adjudicator:
    evaluators: Mapping[str, opportunity_evaluator] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluators", MappingProxyType(dict(self.evaluators)))


__all__ = [
    "EXPERIMENT_ADJUDICATION_API_VERSION",
    "experiment_adjudication_context",
    "experiment_adjudicator",
]
