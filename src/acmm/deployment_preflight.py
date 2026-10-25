from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from acmm.subjects.catalog import subject_execution_binding
from acmm_infra.common import infrastructure_error
from acmm_infra.deployment import read_experiment_apparatus_state
from acmm_infra.profiles.common import profile_error
from acmm_infra.profiles.experiments import experiment_apparatus_sha256


@dataclass(frozen=True)
class subject_deployment_observation:
    subject_id: str
    infrastructure_subject: str
    deployment_root: Path
    experiment_id: str | None
    application_scope: str | None
    observed_apparatus_sha256: str | None
    issues: tuple[str, ...]


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

    observed_sha256 = state.get("source_sha256")
    if not isinstance(observed_sha256, str) or not observed_sha256:
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
    )


def inspect_deployment_contexts(
    *,
    repo_root: Path,
    runtime_root: Path,
    experiment_id: str,
    subject_bindings: Mapping[str, subject_execution_binding],
) -> deployment_preflight_result:
    subjects = tuple(
        _inspect_subject_deployment_context(
            repo_root=repo_root,
            runtime_root=runtime_root,
            experiment_id=experiment_id,
            subject_id=subject_id,
            binding=binding,
        )
        for subject_id, binding in subject_bindings.items()
    )
    return deployment_preflight_result(
        experiment_id=experiment_id,
        subjects=subjects,
    )


def _inspect_subject_deployment_context(
    *,
    repo_root: Path,
    runtime_root: Path,
    experiment_id: str,
    subject_id: str,
    binding: subject_execution_binding,
) -> subject_deployment_context:
    deployment_root = runtime_root / binding.infrastructure_subject
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
            expected_apparatus_sha256=None,
            issues=(str(exc),),
        )

    try:
        expected_sha256 = experiment_apparatus_sha256(
            repo_root,
            binding.infrastructure_subject,
            experiment_id,
        )
    except profile_error as exc:
        expected_sha256 = None
        issues.append(str(exc))

    if state is None:
        issues.append(
            "no active experiment deployment context is recorded"
        )
        return subject_deployment_context(
            subject_id=subject_id,
            infrastructure_subject=binding.infrastructure_subject,
            deployment_root=deployment_root,
            experiment_id=None,
            application_scope=None,
            observed_apparatus_sha256=None,
            expected_apparatus_sha256=expected_sha256,
            issues=tuple(issues),
        )

    observed_experiment_id = state.get("experiment_id")
    if not isinstance(observed_experiment_id, str) or not observed_experiment_id:
        observed_experiment_id = None
        issues.append("deployment context has an invalid experiment ID")
    elif observed_experiment_id != experiment_id:
        issues.append(
            f"active experiment is '{observed_experiment_id}', expected "
            f"'{experiment_id}'"
        )

    application_scope = state.get("application_scope")
    if not isinstance(application_scope, str):
        application_scope = None
        issues.append("deployment context has an invalid application scope")
    elif application_scope != "experiment":
        issues.append(
            f"application scope is '{application_scope}', expected 'experiment'"
        )

    observed_sha256 = state.get("source_sha256")
    if not isinstance(observed_sha256, str) or not observed_sha256:
        observed_sha256 = None
        issues.append("deployment context has an invalid apparatus SHA-256")
    elif expected_sha256 is not None and observed_sha256 != expected_sha256:
        issues.append(
            f"apparatus SHA-256 is {observed_sha256}, expected {expected_sha256}"
        )

    return subject_deployment_context(
        subject_id=subject_id,
        infrastructure_subject=binding.infrastructure_subject,
        deployment_root=deployment_root,
        experiment_id=observed_experiment_id,
        application_scope=application_scope,
        observed_apparatus_sha256=observed_sha256,
        expected_apparatus_sha256=expected_sha256,
        issues=tuple(issues),
    )
