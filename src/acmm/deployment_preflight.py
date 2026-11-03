from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from acmm.experiment_package import deployment_provenance, experiment_package
from acmm.subjects.catalog import subject_execution_binding
from acmm_infra.common import infrastructure_error
from acmm_infra.deployment import read_experiment_apparatus_state


@dataclass(frozen=True)
class subject_deployment_observation:
    subject_id: str
    infrastructure_subject: str
    deployment_root: Path
    experiment_id: str | None
    application_scope: str | None
    observed_apparatus_sha256: str | None
    issues: tuple[str, ...]
    package_sha256: str | None = None


@dataclass(frozen=True)
class deployment_context_discovery:
    subjects: tuple[subject_deployment_observation, ...]

    @property
    def experiment_ids(self) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()

        for subject in self.subjects:
            if subject.experiment_id is None or subject.experiment_id in seen:
                continue

            ordered.append(subject.experiment_id)
            seen.add(subject.experiment_id)

        return tuple(ordered)

    @property
    def package_sha256s(self) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()
        for subject in self.subjects:
            value = subject.package_sha256
            if value is None or value in seen:
                continue
            ordered.append(value)
            seen.add(value)
        return tuple(ordered)


@dataclass(frozen=True)
class subject_deployment_context:
    subject_id: str
    infrastructure_subject: str
    deployment_root: Path
    experiment_id: str | None
    application_scope: str | None
    observed_apparatus_sha256: str | None
    expected_apparatus_sha256: str | None
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
    subjects = tuple(
        _observe_subject_deployment_context(
            runtime_root=runtime_root,
            subject_id=subject_id,
            binding=binding,
        )
        for subject_id, binding in subject_bindings.items()
    )
    return deployment_context_discovery(subjects=subjects)


def _observe_subject_deployment_context(
    *,
    runtime_root: Path,
    subject_id: str,
    binding: subject_execution_binding,
) -> subject_deployment_observation:
    deployment_root = runtime_root / binding.infrastructure_subject

    try:
        state = read_experiment_apparatus_state(deployment_root)
    except infrastructure_error as exc:
        return subject_deployment_observation(
            subject_id=subject_id,
            infrastructure_subject=binding.infrastructure_subject,
            deployment_root=deployment_root,
            experiment_id=None,
            application_scope=None,
            observed_apparatus_sha256=None,
            issues=(str(exc),),
        )

    if state is None:
        return subject_deployment_observation(
            subject_id=subject_id,
            infrastructure_subject=binding.infrastructure_subject,
            deployment_root=deployment_root,
            experiment_id=None,
            application_scope=None,
            observed_apparatus_sha256=None,
            issues=("no active experiment deployment context is recorded",),
        )

    issues: list[str] = []
    experiment_id = state.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        experiment_id = None
        issues.append("deployment context has an invalid experiment ID")

    application_scope = state.get("application_scope")
    if not isinstance(application_scope, str):
        application_scope = None
        issues.append("deployment context has an invalid application scope")

    package_sha256 = None
    package = state.get("package")
    expected_package_apparatus_sha256 = None
    package_managed = isinstance(package, dict)
    if package_managed:
        value = package.get("sha256")
        if isinstance(value, str) and value:
            package_sha256 = value
        apparatus_value = package.get("apparatus_sha256")
        if isinstance(apparatus_value, str) and apparatus_value:
            expected_package_apparatus_sha256 = apparatus_value

    observed_sha256 = state.get("source_sha256")
    if package_managed and expected_package_apparatus_sha256 is None:
        if observed_sha256 is not None:
            issues.append(
                "deployment context records apparatus for a package that declares "
                "none for this subject"
            )
            observed_sha256 = None
    elif not isinstance(observed_sha256, str) or not observed_sha256:
        observed_sha256 = None
        issues.append("deployment context has an invalid apparatus SHA-256")

    return subject_deployment_observation(
        subject_id=subject_id,
        infrastructure_subject=binding.infrastructure_subject,
        deployment_root=deployment_root,
        experiment_id=experiment_id,
        application_scope=application_scope,
        observed_apparatus_sha256=observed_sha256,
        issues=tuple(issues),
        package_sha256=package_sha256,
    )


def inspect_package_deployment_contexts(
    *,
    runtime_root: Path,
    package: experiment_package,
    subject_bindings: Mapping[str, subject_execution_binding],
) -> deployment_preflight_result:
    subjects = tuple(
        _inspect_package_subject_deployment_context(
            runtime_root=runtime_root,
            package=package,
            subject_id=subject_id,
            binding=binding,
        )
        for subject_id, binding in subject_bindings.items()
    )
    return deployment_preflight_result(
        experiment_id=package.experiment_id,
        subjects=subjects,
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
        state = read_experiment_apparatus_state(deployment_root)
    except infrastructure_error as exc:
        return subject_deployment_context(
            subject_id=subject_id,
            infrastructure_subject=binding.infrastructure_subject,
            deployment_root=deployment_root,
            experiment_id=None,
            application_scope=None,
            observed_apparatus_sha256=None,
            expected_apparatus_sha256=expected.apparatus_sha256,
            issues=(str(exc),),
        )

    if state is None:
        return subject_deployment_context(
            subject_id=subject_id,
            infrastructure_subject=binding.infrastructure_subject,
            deployment_root=deployment_root,
            experiment_id=None,
            application_scope=None,
            observed_apparatus_sha256=None,
            expected_apparatus_sha256=expected.apparatus_sha256,
            issues=("no active experiment deployment context is recorded",),
        )

    observed_experiment_id = state.get("experiment_id")
    if not isinstance(observed_experiment_id, str) or not observed_experiment_id:
        observed_experiment_id = None
        issues.append("deployment context has an invalid experiment ID")
    elif observed_experiment_id != package.experiment_id:
        issues.append(
            f"active experiment is '{observed_experiment_id}', expected "
            f"'{package.experiment_id}'"
        )

    application_scope = state.get("application_scope")
    if not isinstance(application_scope, str):
        application_scope = None
        issues.append("deployment context has an invalid application scope")
    elif application_scope != "experiment":
        issues.append(
            f"application scope is '{application_scope}', expected 'experiment'"
        )

    package_state = state.get("package")
    if not isinstance(package_state, dict):
        package_sha256 = None
        issues.append("deployment context is not Experiment Package-backed")
    else:
        package_sha256 = package_state.get("sha256")
        if package_sha256 != expected.package.package_sha256:
            issues.append(
                f"package SHA-256 is {package_sha256!r}, expected "
                f"'{expected.package.package_sha256}'"
            )
        if package_state.get("schema_version") != expected.package.package_schema_version:
            issues.append("package schema version does not match the staged package")
        if package_state.get("contract_sha256") != expected.package.contract_sha256:
            issues.append("Contract SHA-256 does not match the staged package")
        if package_state.get("runtime_sha256") != expected.package.runtime_sha256:
            issues.append("runtime SHA-256 does not match the staged package")
        if package_state.get("apparatus_subject") != binding.infrastructure_subject:
            issues.append("apparatus subject does not match the selected infrastructure")
        if package_state.get("apparatus_sha256") != expected.apparatus_sha256:
            issues.append("apparatus SHA-256 does not match the staged package")

    observed_sha256 = state.get("source_sha256")
    if expected.apparatus_sha256 is None:
        if observed_sha256 is not None:
            issues.append(
                "deployment context records apparatus for a package that declares "
                "none for this subject"
            )
        observed_sha256 = None
    elif not isinstance(observed_sha256, str) or not observed_sha256:
        observed_sha256 = None
        issues.append("deployment context has an invalid apparatus SHA-256")
    elif observed_sha256 != expected.apparatus_sha256:
        issues.append(
            f"apparatus SHA-256 is {observed_sha256}, expected "
            f"{expected.apparatus_sha256}"
        )

    return subject_deployment_context(
        subject_id=subject_id,
        infrastructure_subject=binding.infrastructure_subject,
        deployment_root=deployment_root,
        experiment_id=observed_experiment_id,
        application_scope=application_scope,
        observed_apparatus_sha256=observed_sha256,
        expected_apparatus_sha256=expected.apparatus_sha256,
        issues=tuple(issues),
        package_sha256=(package_sha256 if isinstance(package_sha256, str) else None),
    )


