from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from acmm.experiment_package import deployment_provenance, experiment_package
from acmm.subjects.catalog import subject_execution_binding
from acmm_infra.common import infrastructure_error
from acmm_infra.deployment import read_experiment_package_state


@dataclass(frozen=True)
class subject_deployment_observation:
    subject_id: str
    infrastructure_subject: str
    deployment_root: Path
    experiment_id: str | None
    issues: tuple[str, ...]
    package_sha256: str | None = None


@dataclass(frozen=True)
class deployment_context_discovery:
    subjects: tuple[subject_deployment_observation, ...]

    @property
    def experiment_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            item.experiment_id
            for item in self.subjects
            if item.experiment_id is not None
        ))

    @property
    def package_sha256s(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            item.package_sha256
            for item in self.subjects
            if item.package_sha256 is not None
        ))


@dataclass(frozen=True)
class subject_deployment_context:
    subject_id: str
    infrastructure_subject: str
    deployment_root: Path
    experiment_id: str | None
    issues: tuple[str, ...]
    package_sha256: str | None = None

    @property
    def is_ready(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class deployment_preflight_result:
    experiment_id: str
    subjects: tuple[subject_deployment_context, ...]

    @property
    def is_ready(self) -> bool:
        return all(subject.is_ready for subject in self.subjects)


def discover_deployment_contexts(
    *,
    runtime_root: Path,
    subject_bindings: Mapping[str, subject_execution_binding],
) -> deployment_context_discovery:
    return deployment_context_discovery(subjects=tuple(
        _observe_subject_deployment_context(
            runtime_root=runtime_root,
            subject_id=subject_id,
            binding=binding,
        )
        for subject_id, binding in subject_bindings.items()
    ))


def _observe_subject_deployment_context(
    *,
    runtime_root: Path,
    subject_id: str,
    binding: subject_execution_binding,
) -> subject_deployment_observation:
    deployment_root = runtime_root / binding.infrastructure_subject
    try:
        state = read_experiment_package_state(deployment_root)
    except infrastructure_error as exc:
        return subject_deployment_observation(
            subject_id,
            binding.infrastructure_subject,
            deployment_root,
            None,
            (str(exc),),
        )

    if state is None:
        return subject_deployment_observation(
            subject_id,
            binding.infrastructure_subject,
            deployment_root,
            None,
            ("no active experiment deployment context is recorded",),
        )

    issues: list[str] = []
    experiment_id = state.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        experiment_id = None
        issues.append("deployment context has an invalid experiment ID")

    package = state.get("package")
    package_sha256 = package.get("sha256") if isinstance(package, dict) else None
    if not isinstance(package_sha256, str) or not package_sha256:
        package_sha256 = None
        issues.append("deployment context has invalid package provenance")

    return subject_deployment_observation(
        subject_id,
        binding.infrastructure_subject,
        deployment_root,
        experiment_id,
        tuple(issues),
        package_sha256,
    )


def inspect_package_deployment_contexts(
    *,
    runtime_root: Path,
    package: experiment_package,
    subject_bindings: Mapping[str, subject_execution_binding],
) -> deployment_preflight_result:
    return deployment_preflight_result(
        experiment_id=package.experiment_id,
        subjects=tuple(
            _inspect_package_subject_deployment_context(
                runtime_root=runtime_root,
                package=package,
                subject_id=subject_id,
                binding=binding,
            )
            for subject_id, binding in subject_bindings.items()
        ),
    )


def _inspect_package_subject_deployment_context(
    *,
    runtime_root: Path,
    package: experiment_package,
    subject_id: str,
    binding: subject_execution_binding,
) -> subject_deployment_context:
    deployment_root = runtime_root / binding.infrastructure_subject
    expected = deployment_provenance(package, binding.infrastructure_subject)
    issues: list[str] = []

    try:
        state = read_experiment_package_state(deployment_root)
    except infrastructure_error as exc:
        return subject_deployment_context(
            subject_id,
            binding.infrastructure_subject,
            deployment_root,
            None,
            (str(exc),),
        )

    if state is None:
        return subject_deployment_context(
            subject_id,
            binding.infrastructure_subject,
            deployment_root,
            None,
            ("no active experiment deployment context is recorded",),
        )

    observed_experiment_id = state.get("experiment_id")
    if observed_experiment_id != package.experiment_id:
        issues.append(
            f"active experiment is {observed_experiment_id!r}, expected "
            f"'{package.experiment_id}'"
        )

    package_state = state.get("package")
    package_sha256 = package_state.get("sha256") if isinstance(package_state, dict) else None
    if not isinstance(package_state, dict):
        issues.append("deployment context has invalid package provenance")
    else:
        checks = (
            ("schema_version", "schema version", expected.package.package_schema_version),
            ("sha256", "SHA-256", expected.package.package_sha256),
            ("contract_sha256", "Contract SHA-256", expected.package.contract_sha256),
            ("runtime_sha256", "runtime SHA-256", expected.package.runtime_sha256),
            ("subject", "subject", binding.infrastructure_subject),
        )
        for field, label, expected_value in checks:
            if package_state.get(field) != expected_value:
                issues.append(f"package {label} does not match the staged package")
        expected_integrations = [
            {"id": item.integration_id, "sha256": item.sha256}
            for item in expected.integrations
        ]
        if package_state.get("integrations") != expected_integrations:
            issues.append("integration provenance does not match the staged package")

    return subject_deployment_context(
        subject_id,
        binding.infrastructure_subject,
        deployment_root,
        observed_experiment_id if isinstance(observed_experiment_id, str) else None,
        tuple(issues),
        package_sha256 if isinstance(package_sha256, str) else None,
    )
