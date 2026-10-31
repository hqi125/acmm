from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from acmm.contract.loader import load_contract
from acmm.experiment_package import (
    deployment_provenance,
    experiment_package,
    load_staged_experiment_apparatus,
    open_staged_experiment_package,
)
from acmm.experiments.catalog import lookup_experiment_execution_binding
from acmm.subjects.catalog import lookup_subject_execution_binding
from acmm_infra.agent_zero import agent_zero_manager
from acmm_infra.deployment import read_experiment_apparatus_state
from acmm_infra.docker import docker_client
from acmm_infra.hermes import hermes_manager
from acmm_infra.open_webui import open_webui_manager
from acmm_infra.profiles.agent_zero import load_agent_zero_profile
from acmm_infra.profiles.common import profile_error
from acmm_infra.profiles.experiments import (
    with_agent_zero_apparatus,
    with_agent_zero_experiment,
    with_hermes_apparatus,
    with_hermes_experiment,
    with_open_webui_apparatus,
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

    def up(self, *, timeout_seconds: float = 120.0) -> None:
        ...

    def down(self) -> None:
        ...


@dataclass(frozen=True)
class subject_manager_context:
    infrastructure_subject: str
    experiment_id: str | None
    manager: infrastructure_manager
    llm_baseline_id: str = "gpt_oss_120b_cloud_v1"
    package_sha256: str | None = None


def build_subject_manager(
    repo_root: str | Path,
    runtime_root: str | Path,
    infrastructure_subject: str,
    experiment_id: str | None,
    llm_baseline_id: str | None = None,
) -> subject_manager_context:
    repo = Path(repo_root).expanduser().resolve()
    runtime = Path(runtime_root).expanduser().resolve()
    builder = _MANAGER_BUILDERS.get(infrastructure_subject)
    if builder is None:
        raise profile_error(
            "no infrastructure readiness builder is registered for subject "
            f"'{infrastructure_subject}'."
        )
    resolved_baseline_id = llm_baseline_id
    if resolved_baseline_id is None and experiment_id is not None:
        resolved_baseline_id = experiment_llm_baseline_id(
            repo,
            experiment_id,
            infrastructure_subject,
        )
    manager = builder(repo, runtime, experiment_id, resolved_baseline_id)
    return subject_manager_context(
        infrastructure_subject=infrastructure_subject,
        experiment_id=experiment_id,
        llm_baseline_id=manager.profile.llm_baseline_id,
        manager=manager,
    )


def build_package_subject_manager(
    repo_root: str | Path,
    runtime_root: str | Path,
    infrastructure_subject: str,
    package: experiment_package,
) -> subject_manager_context:
    repo = Path(repo_root).expanduser().resolve()
    runtime = Path(runtime_root).expanduser().resolve()
    baseline_id = experiment_package_llm_baseline_id(
        package,
        infrastructure_subject,
    )
    component = package.apparatus.get(infrastructure_subject)
    provenance = deployment_provenance(package, infrastructure_subject)

    if infrastructure_subject == "hermes":
        profile = load_hermes_profile(repo, baseline_id)
        if component is not None:
            component = load_staged_experiment_apparatus(
                package,
                infrastructure_subject,
            )
            profile = with_hermes_apparatus(
                component.root,
                profile,
                package.experiment_id,
            )
        manager = hermes_manager(
            repo,
            runtime,
            profile,
            docker_client(repo, runtime, profile),
            experiment_id=package.experiment_id,
            deployment_provenance=provenance,
        )
    elif infrastructure_subject == "open-webui":
        profile = load_open_webui_profile(repo, baseline_id)
        if component is not None:
            component = load_staged_experiment_apparatus(
                package,
                infrastructure_subject,
            )
            profile = with_open_webui_apparatus(
                component.root,
                profile,
                package.experiment_id,
            )
        manager = open_webui_manager(
            repo,
            runtime,
            profile,
            docker_client(repo, runtime, profile),
            experiment_id=package.experiment_id,
            deployment_provenance=provenance,
        )
    elif infrastructure_subject == "agent-zero":
        profile = load_agent_zero_profile(repo, baseline_id)
        if component is not None:
            component = load_staged_experiment_apparatus(
                package,
                infrastructure_subject,
            )
            profile = with_agent_zero_apparatus(
                component.root,
                profile,
                package.experiment_id,
            )
        manager = agent_zero_manager(
            repo,
            runtime,
            profile,
            docker_client(repo, runtime, profile),
            experiment_id=package.experiment_id,
            deployment_provenance=provenance,
        )
    else:
        raise profile_error(
            "no infrastructure readiness builder is registered for subject "
            f"'{infrastructure_subject}'."
        )

    return subject_manager_context(
        infrastructure_subject=infrastructure_subject,
        experiment_id=package.experiment_id,
        llm_baseline_id=baseline_id,
        package_sha256=package.package_sha256,
        manager=manager,
    )


def build_deployed_subject_manager(
    repo_root: str | Path,
    runtime_root: str | Path,
    infrastructure_subject: str,
    llm_baseline_id: str | None = None,
) -> subject_manager_context:
    baseline = build_subject_manager(
        repo_root,
        runtime_root,
        infrastructure_subject,
        None,
        llm_baseline_id,
    )
    state = read_experiment_apparatus_state(baseline.manager.deployment_root)
    if state is None:
        return baseline

    experiment_id = _state_experiment_id(state)
    package_state = state.get("package")
    if isinstance(package_state, dict):
        package = _open_deployed_package(
            repo_root,
            runtime_root,
            experiment_id,
            infrastructure_subject,
            state,
            package_state,
        )
        return build_package_subject_manager(
            repo_root,
            runtime_root,
            infrastructure_subject,
            package,
        )

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
    llm_baseline_id: str | None = None,
) -> dict[str, object] | None:
    baseline = build_subject_manager(
        repo_root,
        runtime_root,
        infrastructure_subject,
        None,
        llm_baseline_id,
    )
    return read_experiment_apparatus_state(baseline.manager.deployment_root)


def experiment_package_llm_baseline_id(
    package: experiment_package,
    infrastructure_subject: str,
) -> str:
    matches: list[str] = []
    for subject in package.contract.get("subjects", []):
        subject_id = subject.get("id") if isinstance(subject, dict) else None
        if not isinstance(subject_id, str):
            continue
        binding = lookup_subject_execution_binding(subject_id)
        if binding is None or binding.infrastructure_subject != infrastructure_subject:
            continue
        matches.append(binding.llm_baseline_id)

    unique = tuple(dict.fromkeys(matches))
    if len(unique) != 1:
        raise profile_error(
            f"Experiment Package '{package.experiment_id}' does not resolve exactly "
            f"one LLM baseline for infrastructure subject "
            f"'{infrastructure_subject}'."
        )
    return unique[0]


def _state_experiment_id(state: dict[str, object]) -> str:
    experiment_id = state.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise profile_error("Deployed experiment state has an invalid experiment ID.")
    return experiment_id


def _open_deployed_package(
    repo_root: str | Path,
    runtime_root: str | Path,
    experiment_id: str,
    infrastructure_subject: str,
    state: dict[str, object],
    package_state: dict[str, object],
) -> experiment_package:
    package_sha256 = package_state.get("sha256")
    if not isinstance(package_sha256, str) or not package_sha256:
        raise profile_error("Deployed Experiment Package state has an invalid SHA-256.")

    repo = Path(repo_root).expanduser().resolve()
    package = open_staged_experiment_package(
        runtime_root,
        experiment_id,
        package_sha256,
        contract_schema_path=(repo / "contracts" / "v0_1" / "contract.schema.json"),
    )
    expected = deployment_provenance(package, infrastructure_subject)
    if package_state.get("schema_version") != expected.package.package_schema_version:
        raise profile_error(
            "Deployed Experiment Package schema version does not match its staged "
            "snapshot."
        )
    if package_state.get("contract_sha256") != expected.package.contract_sha256:
        raise profile_error(
            "Deployed Experiment Package Contract SHA-256 does not match its staged "
            "snapshot."
        )
    if package_state.get("runtime_sha256") != expected.package.runtime_sha256:
        raise profile_error(
            "Deployed Experiment Package runtime SHA-256 does not match its staged "
            "snapshot."
        )
    if package_state.get("apparatus_subject") != infrastructure_subject:
        raise profile_error(
            "Deployed Experiment Package apparatus subject does not match the "
            "deployment subject."
        )
    if package_state.get("apparatus_sha256") != expected.apparatus_sha256:
        raise profile_error(
            "Deployed Experiment Package apparatus SHA-256 does not match its staged "
            "snapshot."
        )
    if state.get("source_sha256") != expected.apparatus_sha256:
        raise profile_error(
            "Deployed apparatus source SHA-256 does not match the staged Experiment "
            "Package apparatus."
        )
    return package


def experiment_llm_baseline_id(
    repo_root: str | Path,
    experiment_id: str,
    infrastructure_subject: str,
) -> str:
    repo = Path(repo_root).expanduser().resolve()
    experiment = lookup_experiment_execution_binding(experiment_id)
    if experiment is None:
        raise profile_error(
            f"Experiment '{experiment_id}' is not registered in the ACMM catalog."
        )
    contract = load_contract((repo / experiment.canonical_contract).resolve())
    matches: list[str] = []
    for subject in contract.get("subjects", []):
        subject_id = subject.get("id") if isinstance(subject, dict) else None
        if not isinstance(subject_id, str):
            continue
        binding = lookup_subject_execution_binding(subject_id)
        if binding is None or binding.infrastructure_subject != infrastructure_subject:
            continue
        matches.append(binding.llm_baseline_id)
    unique = tuple(dict.fromkeys(matches))
    if len(unique) != 1:
        raise profile_error(
            f"Experiment '{experiment_id}' does not resolve exactly one LLM baseline "
            f"for infrastructure subject '{infrastructure_subject}'."
        )
    return unique[0]


def _hermes_manager(
    repo: Path,
    runtime: Path,
    experiment_id: str | None,
    llm_baseline_id: str | None,
):
    profile = load_hermes_profile(repo, llm_baseline_id)
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


def _open_webui_manager(
    repo: Path,
    runtime: Path,
    experiment_id: str | None,
    llm_baseline_id: str | None,
):
    profile = load_open_webui_profile(repo, llm_baseline_id)
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


def _agent_zero_manager(
    repo: Path,
    runtime: Path,
    experiment_id: str | None,
    llm_baseline_id: str | None,
):
    profile = load_agent_zero_profile(repo, llm_baseline_id)
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
