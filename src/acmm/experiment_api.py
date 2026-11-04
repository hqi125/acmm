from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from acmm.evidence.artifacts import (
    find_evidence,
    load_conversation_messages,
    load_json_object_array_evidence,
    load_text_evidence,
)
from acmm.evidence.interpretation import (
    interpreted_structured_output,
    semantic_scalar_normalization,
)
from acmm.evidence.semantic import (
    interpret_semantic_scalar,
    interpreted_semantic_scalar,
    output_scalar_semantics,
    output_scalar_semantics_for_path,
    semantic_scalar_equal,
)
from acmm.evidence.structured import (
    decode_json_object_text,
    json_object_decode_result,
)
from acmm.evidence.text import (
    contains_numeric_value,
    normalize_text_for_matching,
    numeric_values_share_text_unit,
)
from acmm.harness.capabilities import harness_capabilities
from acmm.runtime.interfaces import (
    captured_evidence,
    environment_fixture,
    evaluation_context,
    judgment_result,
    opportunity_evaluator,
    prepared_environment,
)


EXPERIMENT_RUNTIME_API_VERSION = "0.1"


@dataclass(frozen=True)
class experiment_runtime_context:
    experiment_id: str
    package_sha256: str
    package_root: Path

    def resolve_resource(
        self,
        relative_path: str | Path,
    ) -> Path:
        """Resolve one existing package-relative runtime resource."""

        value = str(relative_path)
        relative = Path(relative_path)
        if (
            not value.strip()
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("Experiment resource path must be package-relative and safe.")

        package_root = self.package_root.resolve()
        source_resource = package_root / relative
        _reject_resource_symlinks(source_resource, package_root)
        resource = source_resource.resolve()

        try:
            resource.relative_to(package_root)
        except ValueError as exc:
            raise ValueError("Experiment resource path resolves outside the package root.") from exc

        if not resource.exists():
            raise ValueError(f"Experiment resource does not exist: '{relative}'.")

        return resource


def _reject_resource_symlinks(
    resource: Path,
    package_root: Path,
) -> None:
    current = resource
    while current != package_root:
        if current.is_symlink():
            raise ValueError("Experiment resource path must not traverse symbolic links.")
        current = current.parent


@dataclass(frozen=True)
class experiment_runtime:
    capabilities: harness_capabilities
    fixtures: Mapping[str, environment_fixture] = field(default_factory=dict)
    evaluators: Mapping[str, opportunity_evaluator] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fixtures",
            MappingProxyType(dict(self.fixtures)),
        )
        object.__setattr__(
            self,
            "evaluators",
            MappingProxyType(dict(self.evaluators)),
        )


__all__ = [
    "EXPERIMENT_RUNTIME_API_VERSION",
    "captured_evidence",
    "contains_numeric_value",
    "decode_json_object_text",
    "environment_fixture",
    "evaluation_context",
    "experiment_runtime",
    "experiment_runtime_context",
    "find_evidence",
    "harness_capabilities",
    "interpret_semantic_scalar",
    "interpreted_semantic_scalar",
    "interpreted_structured_output",
    "json_object_decode_result",
    "judgment_result",
    "load_conversation_messages",
    "load_json_object_array_evidence",
    "load_text_evidence",
    "normalize_text_for_matching",
    "numeric_values_share_text_unit",
    "output_scalar_semantics",
    "output_scalar_semantics_for_path",
    "semantic_scalar_equal",
    "semantic_scalar_normalization",
    "opportunity_evaluator",
    "prepared_environment",
]
