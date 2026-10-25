from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from acmm_infra.agent_zero import agent_zero_manager
from acmm_infra.common import doctor_report, infrastructure_error
from acmm_infra.deployment import active_experiment_id
from acmm_infra.docker import docker_client
from acmm_infra.hermes import hermes_manager
from acmm_infra.open_webui import open_webui_manager
from acmm_infra.profiles.agent_zero import load_agent_zero_profile
from acmm_infra.profiles.common import profile_error
from acmm_infra.profiles.experiments import (
    with_agent_zero_experiment,
    with_hermes_experiment,
    with_open_webui_experiment,
)
from acmm_infra.profiles.hermes import load_hermes_profile
from acmm_infra.profiles.open_webui import load_open_webui_profile


@dataclass(frozen=True)
class deployed_subject_doctor_result:
    infrastructure_subject: str
    experiment_id: str | None
    report: doctor_report | None
    error: str | None

    @property
    def is_ready(self) -> bool:
        return self.error is None and self.report is not None and self.report.is_ready


def doctor_deployed_subject(
    repo_root: str | Path,
    runtime_root: str | Path,
    infrastructure_subject: str,
) -> deployed_subject_doctor_result:
    repo = Path(repo_root).expanduser().resolve()
    runtime = Path(runtime_root).expanduser().resolve()
    builder = _MANAGER_BUILDERS.get(infrastructure_subject)

    if builder is None:
        return deployed_subject_doctor_result(
            infrastructure_subject=infrastructure_subject,
            experiment_id=None,
            report=None,
            error=(
                "no infrastructure readiness builder is registered for subject "
                f"'{infrastructure_subject}'"
            ),
        )

    try:
        experiment_id, manager = builder(repo, runtime)
    except (infrastructure_error, profile_error, OSError, ValueError) as exc:
        return deployed_subject_doctor_result(
            infrastructure_subject=infrastructure_subject,
            experiment_id=None,
            report=None,
            error=str(exc),
        )

    try:
        report = manager.doctor()
    except (infrastructure_error, OSError, ValueError) as exc:
        return deployed_subject_doctor_result(
            infrastructure_subject=infrastructure_subject,
            experiment_id=experiment_id,
            report=None,
            error=str(exc),
        )

    return deployed_subject_doctor_result(
        infrastructure_subject=infrastructure_subject,
        experiment_id=experiment_id,
        report=report,
        error=None,
    )


def _hermes_manager(
    repo_root: Path,
    runtime_root: Path,
) -> tuple[str | None, hermes_manager]:
    profile = load_hermes_profile(repo_root)
    deployment_root = (runtime_root / profile.deployment_directory).resolve()
    experiment_id = active_experiment_id(deployment_root)
    if experiment_id is not None:
        profile = with_hermes_experiment(repo_root, profile, experiment_id)
    docker = docker_client(repo_root, runtime_root, profile)
    return experiment_id, hermes_manager(
        repo_root,
        runtime_root,
        profile,
        docker,
        experiment_id=experiment_id,
    )


def _open_webui_manager(
    repo_root: Path,
    runtime_root: Path,
) -> tuple[str | None, open_webui_manager]:
    profile = load_open_webui_profile(repo_root)
    deployment_root = (runtime_root / profile.deployment_directory).resolve()
    experiment_id = active_experiment_id(deployment_root)
    if experiment_id is not None:
        profile = with_open_webui_experiment(repo_root, profile, experiment_id)
    docker = docker_client(repo_root, runtime_root, profile)
    return experiment_id, open_webui_manager(
        repo_root,
        runtime_root,
        profile,
        docker,
        experiment_id=experiment_id,
    )


def _agent_zero_manager(
    repo_root: Path,
    runtime_root: Path,
) -> tuple[str | None, agent_zero_manager]:
    profile = load_agent_zero_profile(repo_root)
    deployment_root = (runtime_root / profile.deployment_directory).resolve()
    experiment_id = active_experiment_id(deployment_root)
    if experiment_id is not None:
        profile = with_agent_zero_experiment(repo_root, profile, experiment_id)
    docker = docker_client(repo_root, runtime_root, profile)
    return experiment_id, agent_zero_manager(
        repo_root,
        runtime_root,
        profile,
        docker,
        experiment_id=experiment_id,
    )


class _doctor_manager(Protocol):
    def doctor(self) -> doctor_report:
        ...


_manager_builder = Callable[[Path, Path], tuple[str | None, _doctor_manager]]
_MANAGER_BUILDERS: dict[str, _manager_builder] = {
    "hermes": _hermes_manager,
    "open-webui": _open_webui_manager,
    "agent-zero": _agent_zero_manager,
}
