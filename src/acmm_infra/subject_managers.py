from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from acmm_infra.agent_zero import agent_zero_manager
from acmm_infra.deployment import active_experiment_id, read_experiment_apparatus_state
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


class infrastructure_manager(Protocol):
    deployment_root: Path
    docker: docker_client

    def apply(self, *, timeout_seconds: float = 120.0, progress=None) -> dict[str, Any]:
        ...

    def status(self) -> dict[str, Any]:
        ...

    def doctor(self):
        ...


@dataclass(frozen=True)
class subject_manager_context:
    infrastructure_subject: str
    experiment_id: str | None
    manager: infrastructure_manager


def build_subject_manager(
    repo_root: str | Path,
    runtime_root: str | Path,
    infrastructure_subject: str,
    experiment_id: str | None,
) -> subject_manager_context:
    repo = Path(repo_root).expanduser().resolve()
    runtime = Path(runtime_root).expanduser().resolve()
    builder = _MANAGER_BUILDERS.get(infrastructure_subject)
    if builder is None:
        raise profile_error(
            "no infrastructure readiness builder is registered for subject "
            f"'{infrastructure_subject}'."
        )
    manager = builder(repo, runtime, experiment_id)
    return subject_manager_context(
        infrastructure_subject=infrastructure_subject,
        experiment_id=experiment_id,
        manager=manager,
    )


def build_deployed_subject_manager(
    repo_root: str | Path,
    runtime_root: str | Path,
    infrastructure_subject: str,
) -> subject_manager_context:
    baseline = build_subject_manager(
        repo_root,
        runtime_root,
        infrastructure_subject,
        None,
    )
    experiment_id = active_experiment_id(baseline.manager.deployment_root)
    if experiment_id is None:
        return baseline
    return build_subject_manager(
        repo_root,
        runtime_root,
        infrastructure_subject,
        experiment_id,
    )


def deployed_experiment_state(
    repo_root: str | Path,
    runtime_root: str | Path,
    infrastructure_subject: str,
) -> dict[str, object] | None:
    baseline = build_subject_manager(
        repo_root,
        runtime_root,
        infrastructure_subject,
        None,
    )
    return read_experiment_apparatus_state(baseline.manager.deployment_root)


def _hermes_manager(repo: Path, runtime: Path, experiment_id: str | None):
    profile = load_hermes_profile(repo)
    if experiment_id is not None:
        profile = with_hermes_experiment(repo, profile, experiment_id)
    docker = docker_client(repo, runtime, profile)
    return hermes_manager(
        repo,
        runtime,
        profile,
        docker,
        experiment_id=experiment_id,
    )


def _open_webui_manager(repo: Path, runtime: Path, experiment_id: str | None):
    profile = load_open_webui_profile(repo)
    if experiment_id is not None:
        profile = with_open_webui_experiment(repo, profile, experiment_id)
    docker = docker_client(repo, runtime, profile)
    return open_webui_manager(
        repo,
        runtime,
        profile,
        docker,
        experiment_id=experiment_id,
    )


def _agent_zero_manager(repo: Path, runtime: Path, experiment_id: str | None):
    profile = load_agent_zero_profile(repo)
    if experiment_id is not None:
        profile = with_agent_zero_experiment(repo, profile, experiment_id)
    docker = docker_client(repo, runtime, profile)
    return agent_zero_manager(
        repo,
        runtime,
        profile,
        docker,
        experiment_id=experiment_id,
    )


_MANAGER_BUILDERS = {
    "hermes": _hermes_manager,
    "open-webui": _open_webui_manager,
    "agent-zero": _agent_zero_manager,
}
