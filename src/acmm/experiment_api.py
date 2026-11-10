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
from acmm.secret_bindings import (
    experiment_secret_binding_key,
    resolve_secret,
    secret_binding_key,
)
from acmm.runtime.interfaces import (
    apparatus_action_progress,
    apparatus_action_result,
    captured_evidence,
    environment_finalizer,
    environment_fixture,
    environment_observer,
    evaluation_context,
    judgment_result,
    observed_environment,
    opportunity_evaluator,
    prepared_environment,
    runtime_apparatus_action,
    runtime_diagnostic,
    runtime_diagnostic_check,
    runtime_diagnostic_report,
    runtime_execution_lifecycle,
    runtime_run_context,
    runtime_run_finalization,
    runtime_session_context,
    runtime_session_finalization,
)


EXPERIMENT_RUNTIME_API_VERSION = "0.1"


@dataclass(frozen=True)
class experiment_runtime_context:
    experiment_id: str
    package_sha256: str
    package_root: Path
    package_secret_ids: tuple[str, ...] = ()
    integration_secret_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_secret_ids", tuple(self.package_secret_ids))
        normalized = {
            integration_id: tuple(secret_ids)
            for integration_id, secret_ids in self.integration_secret_ids.items()
        }
        object.__setattr__(
            self,
            "integration_secret_ids",
            MappingProxyType(normalized),
        )

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

    def resolve_package_secret(self, secret_id: str) -> str:
        if secret_id not in self.package_secret_ids:
            raise ValueError(
                f"Experiment Package did not declare secret '{secret_id}'."
            )
        return resolve_secret(
            experiment_secret_binding_key(
                experiment_id=self.experiment_id,
                secret_id=secret_id,
            )
        )

    def resolve_integration_secret(
        self,
        integration_id: str,
        secret_id: str,
    ) -> str:
        declared = self.integration_secret_ids.get(integration_id, ())
        if secret_id not in declared:
            raise ValueError(
                f"Integration '{integration_id}' did not declare secret '{secret_id}'."
            )
        return resolve_secret(
            secret_binding_key(
                experiment_id=self.experiment_id,
                integration_id=integration_id,
                secret_id=secret_id,
            )
        )


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
    lifecycle: runtime_execution_lifecycle | None = None
    fixtures: Mapping[str, environment_fixture] = field(default_factory=dict)
    evaluators: Mapping[str, opportunity_evaluator] = field(default_factory=dict)
    diagnostics: Mapping[str, runtime_diagnostic] = field(default_factory=dict)
    apparatus_actions: Mapping[str, runtime_apparatus_action] = field(default_factory=dict)

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
        object.__setattr__(
            self,
            "diagnostics",
            MappingProxyType(dict(self.diagnostics)),
        )
        object.__setattr__(
            self,
            "apparatus_actions",
            MappingProxyType(dict(self.apparatus_actions)),
        )


__all__ = [
    "EXPERIMENT_RUNTIME_API_VERSION",
    "apparatus_action_progress",
    "apparatus_action_result",
    "captured_evidence",
    "contains_numeric_value",
    "decode_json_object_text",
    "environment_finalizer",
    "environment_fixture",
    "environment_observer",
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
    "observed_environment",
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
    "runtime_apparatus_action",
    "runtime_diagnostic",
    "runtime_diagnostic_check",
    "runtime_diagnostic_report",
    "runtime_session_finalization",
    "runtime_session_context",
    "runtime_run_finalization",
    "runtime_run_context",
    "runtime_execution_lifecycle",
]
