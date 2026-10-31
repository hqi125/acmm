from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from acmm.contract.loader import load_contract
from acmm.experiment_package import open_staged_experiment_package
from acmm.experiments.catalog import lookup_experiment_execution_binding
from acmm.subjects.catalog import (
    lookup_subject_execution_binding,
    registered_subject_ids,
)
from acmm_infra.common import infrastructure_error
from acmm_infra.deployment import (
    read_experiment_apparatus_state,
    read_llm_baseline_state,
)
from acmm_infra.docker import docker_error
from acmm_infra.profiles.common import profile_error
from acmm_infra.profiles.llm import (
    current_llm_baseline_id,
    load_llm_baseline,
    primary_requested_context_window,
)
from acmm_infra.profiles.experiments import experiment_apparatus_sha256
from acmm_infra.readiness import (
    deployed_subject_doctor_result,
    doctor_deployed_subject,
)
from acmm_infra.runtime_lifecycle import discover_runtime_lease
from acmm_infra.subject_managers import (
    build_deployed_subject_manager,
    build_subject_manager,
    deployed_experiment_state,
)


@dataclass(frozen=True)
class cohort_subject:
    subject_id: str
    infrastructure_subject: str
    llm_baseline_id: str = "gpt_oss_120b_cloud_v1"


@dataclass(frozen=True)
class experiment_cohort:
    experiment_id: str | None
    canonical_contract: Path | None
    subjects: tuple[cohort_subject, ...]


@dataclass(frozen=True)
class cohort_llm_observation:
    expected_baseline_id: str
    deployed_baseline_id: str
    model_name: str
    logical_provider: str
    minimum_context_window: int
    requested_context_window: int | None
    observed_model_name: str | None
    observed_context_window: int | None
    model_observation_source: str | None
    context_observation_source: str | None
    context_observation_kind: str
    model_matches_baseline: bool | None
    context_meets_minimum: bool | None
    context_matches_requested: bool | None
    observed_utility_model: str | None = None
    observed_utility_context_window: int | None = None
    provider_model_available: bool | None = None
    provider_model_availability_source: str | None = None


@dataclass(frozen=True)
class cohort_subject_status:
    subject_id: str
    infrastructure_subject: str
    running: bool | None
    experiment_id: str | None
    application_scope: str | None
    apparatus_state: str
    source_sha256: str | None
    package_sha256: str | None = None
    llm: cohort_llm_observation | None = None
    error: str | None = None


@dataclass(frozen=True)
class cohort_context_summary:
    state: str
    experiment_id: str | None
    package_sha256: str | None = None


@dataclass(frozen=True)
class cohort_stop_result:
    subject_id: str
    infrastructure_subject: str
    initial_running: bool
    stopped: bool
    error: str | None = None


@dataclass(frozen=True)
class cohort_doctor_result:
    subject: cohort_subject
    doctor: deployed_subject_doctor_result
    deployment_state: dict[str, object] | None
    initially_running: bool | None
    started_by_acmm: bool
    llm: cohort_llm_observation | None = None
    release_error: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.doctor.is_ready and self.release_error is None


def resolve_experiment_cohort(
    repo_root: str | Path,
    experiment_id: str,
    selected_subject_ids: Iterable[str] = (),
) -> experiment_cohort:
    repo = Path(repo_root).expanduser().resolve()
    binding = lookup_experiment_execution_binding(experiment_id)
    if binding is None:
        raise infrastructure_error(
            f"Experiment '{experiment_id}' is not registered in the ACMM catalog."
        )

    contract_path = (repo / binding.canonical_contract).resolve()
    contract = load_contract(contract_path)
    observed_id = contract.get("experiment", {}).get("id")
    if observed_id != experiment_id:
        raise infrastructure_error(
            f"Canonical Contract '{contract_path}' declares experiment "
            f"'{observed_id}', expected '{experiment_id}'."
        )

    for subject in contract["subjects"]:
        _require_contract_llm_binding(repo, subject)

    subject_ids = tuple(item["id"] for item in contract["subjects"])
    subjects = _resolve_subjects(subject_ids, selected_subject_ids)
    return experiment_cohort(
        experiment_id=experiment_id,
        canonical_contract=contract_path,
        subjects=subjects,
    )


def resolve_baseline_cohort(
    repo_root: str | Path,
    selected_subject_ids: Iterable[str] = (),
    *,
    llm_baseline_id: str | None = None,
) -> experiment_cohort:
    baseline_id = llm_baseline_id or current_llm_baseline_id(repo_root)
    load_llm_baseline(repo_root, baseline_id)
    registered = registered_subject_ids(baseline_id)
    if registered:
        subjects = _resolve_subjects(registered, selected_subject_ids)
    else:
        subjects = _resolve_qualification_subjects(
            baseline_id,
            selected_subject_ids,
        )
    return experiment_cohort(
        experiment_id=None,
        canonical_contract=None,
        subjects=subjects,
    )


def resolve_deployed_cohort(
    repo_root: str | Path,
    runtime_root: str | Path,
    selected_subject_ids: Iterable[str] = (),
) -> experiment_cohort:
    repo = Path(repo_root).expanduser().resolve()
    runtime = Path(runtime_root).expanduser().resolve()
    current_baseline = current_llm_baseline_id(repo)
    subjects: list[cohort_subject] = []

    for infrastructure_subject in _INFRASTRUCTURE_SUBJECTS:
        context = build_deployed_subject_manager(
            repo,
            runtime,
            infrastructure_subject,
            current_baseline,
        )
        experiment_id = context.experiment_id
        if experiment_id is not None:
            if context.package_sha256 is not None:
                package = open_staged_experiment_package(
                    runtime,
                    experiment_id,
                    context.package_sha256,
                    contract_schema_path=(
                        repo / "contracts" / "v0_1" / "contract.schema.json"
                    ),
                )
                matches = _package_cohort_subjects(
                    package.contract.get("subjects", []),
                    infrastructure_subject,
                )
            else:
                experiment = resolve_experiment_cohort(repo, experiment_id)
                matches = tuple(
                    item
                    for item in experiment.subjects
                    if item.infrastructure_subject == infrastructure_subject
                )
            if len(matches) != 1:
                raise infrastructure_error(
                    f"Deployed experiment '{experiment_id}' does not resolve exactly "
                    f"one subject for infrastructure '{infrastructure_subject}'."
                )
            subjects.append(matches[0])
            continue

        baseline_id = context.llm_baseline_id
        llm_state = read_llm_baseline_state(context.manager.deployment_root)
        if llm_state is not None:
            value = llm_state.get("llm_baseline_id")
            if isinstance(value, str) and value.strip():
                baseline_id = value
        subjects.append(
            cohort_subject(
                subject_id=_deployed_subject_id(
                    baseline_id,
                    infrastructure_subject,
                ),
                infrastructure_subject=infrastructure_subject,
                llm_baseline_id=baseline_id,
            )
        )

    selected = tuple(dict.fromkeys(selected_subject_ids))
    if selected:
        available = {item.subject_id for item in subjects}
        unknown = tuple(item for item in selected if item not in available)
        if unknown:
            raise infrastructure_error(
                "Selected subject(s) are not present in the deployed cohort: "
                + ", ".join(unknown)
            )
        subjects = [item for item in subjects if item.subject_id in selected]

    return experiment_cohort(
        experiment_id=None,
        canonical_contract=None,
        subjects=tuple(subjects),
    )


def apply_cohort(
    repo_root: str | Path,
    runtime_root: str | Path,
    cohort: experiment_cohort,
    *,
    timeout_seconds: float,
    progress,
) -> tuple[cohort_subject_status, ...]:
    contexts = []
    running: list[str] = []
    for subject in cohort.subjects:
        context = build_subject_manager(
            repo_root,
            runtime_root,
            subject.infrastructure_subject,
            cohort.experiment_id,
            subject.llm_baseline_id,
        )
        contexts.append((subject, context))
        if context.manager.docker.container_running():
            running.append(subject.subject_id)

    if running:
        names = ", ".join(running)
        raise infrastructure_error(
            "Cohort apply requires all selected subject runtimes to be stopped. "
            f"Still running: {names}."
        )

    results: list[cohort_subject_status] = []
    total = len(contexts)
    for index, (subject, context) in enumerate(contexts, start=1):
        progress(index, total, subject, context.manager)
        try:
            context.manager.apply(
                timeout_seconds=timeout_seconds,
                progress=lambda message: progress(None, None, subject, message),
            )
        except (infrastructure_error, profile_error, docker_error, OSError) as exc:
            raise infrastructure_error(
                f"Cohort apply failed for '{subject.subject_id}' after "
                f"{index - 1}/{total} subject(s) were applied. Already applied "
                "subjects are left in their new state; no automatic rollback was "
                f"performed. Cause: {exc}"
            ) from exc

        observed = inspect_subject_context(
            repo_root,
            runtime_root,
            subject,
            include_running=False,
        )
        _require_applied_context(observed, cohort.experiment_id)
        results.append(observed)
    return tuple(results)


def inspect_cohort(
    repo_root: str | Path,
    runtime_root: str | Path,
    subjects: tuple[cohort_subject, ...],
    *,
    include_running: bool = True,
) -> tuple[cohort_subject_status, ...]:
    return tuple(
        inspect_subject_context(
            repo_root,
            runtime_root,
            subject,
            include_running=include_running,
        )
        for subject in subjects
    )


def inspect_subject_context(
    repo_root: str | Path,
    runtime_root: str | Path,
    subject: cohort_subject,
    *,
    include_running: bool = True,
) -> cohort_subject_status:
    try:
        context = build_deployed_subject_manager(
            repo_root,
            runtime_root,
            subject.infrastructure_subject,
            subject.llm_baseline_id,
        )
        status = context.manager.status() if include_running else None
        llm_state = read_llm_baseline_state(context.manager.deployment_root)
        deployed_baseline_id = context.llm_baseline_id
        if llm_state is not None:
            value = llm_state.get("llm_baseline_id")
            if isinstance(value, str) and value.strip():
                deployed_baseline_id = value
        llm = _observe_llm_context(
            repo_root,
            subject,
            deployed_baseline_id,
            status,
        )
        marker = read_experiment_apparatus_state(context.manager.deployment_root)
        experiment_id = context.experiment_id
        package_sha256 = context.package_sha256
        scope = None
        source_sha256 = None
        apparatus_state = "none"
        if marker is not None:
            scope_value = marker.get("application_scope")
            scope = scope_value if isinstance(scope_value, str) else None
            hash_value = marker.get("source_sha256")
            source_sha256 = hash_value if isinstance(hash_value, str) else None
            apparatus_state = _apparatus_state(
                repo_root,
                subject.infrastructure_subject,
                experiment_id,
                source_sha256,
                package_expected_sha256=(
                    context.manager.deployment_provenance.apparatus_sha256
                    if getattr(context.manager, "deployment_provenance", None) is not None
                    else None
                ),
                package_managed=(
                    getattr(context.manager, "deployment_provenance", None) is not None
                ),
            )
        running = None
        if status is not None:
            running = bool(status["observed"]["container_running"])
        return cohort_subject_status(
            subject_id=subject.subject_id,
            infrastructure_subject=subject.infrastructure_subject,
            running=running,
            experiment_id=experiment_id,
            application_scope=scope,
            apparatus_state=apparatus_state,
            source_sha256=source_sha256,
            package_sha256=package_sha256,
            llm=llm,
        )
    except (infrastructure_error, profile_error, OSError, ValueError) as exc:
        return cohort_subject_status(
            subject_id=subject.subject_id,
            infrastructure_subject=subject.infrastructure_subject,
            running=None,
            experiment_id=None,
            application_scope=None,
            apparatus_state="unknown",
            source_sha256=None,
            package_sha256=None,
            llm=None,
            error=str(exc),
        )


def _observe_llm_context(
    repo_root: str | Path,
    subject: cohort_subject,
    deployed_baseline_id: str,
    status: dict[str, Any] | None,
) -> cohort_llm_observation:
    baseline = load_llm_baseline(repo_root, deployed_baseline_id)
    requested = primary_requested_context_window(
        baseline,
        subject.infrastructure_subject,
    )
    observed_model: str | None = None
    observed_context: int | None = None
    model_source: str | None = None
    context_source: str | None = None
    context_kind = "unknown"
    observed_utility_model: str | None = None
    observed_utility_context: int | None = None
    provider_model_available: bool | None = None
    provider_model_source: str | None = None

    if status is not None and subject.infrastructure_subject == "hermes":
        observed = status.get("observed")
        if isinstance(observed, dict) and observed.get("live_observation_ok") is True:
            model_value = observed.get("model_default")
            context_value = observed.get("context_window")
            if isinstance(model_value, str) and model_value:
                observed_model = model_value
                model_source = "hermes_live_config"
            if isinstance(context_value, int) and not isinstance(context_value, bool):
                observed_context = context_value
                context_source = "hermes_live_config:context_window"
                context_kind = "configured_effective"
            availability = observed.get("provider_model_available")
            if isinstance(availability, bool):
                provider_model_available = availability
                provider_model_source = "ollama_api:/api/tags via hermes"

    elif status is not None and subject.infrastructure_subject == "open-webui":
        observed = status.get("observed")
        backend = observed.get("backend") if isinstance(observed, dict) else None
        if isinstance(backend, dict):
            availability = backend.get("model_available")
            if isinstance(availability, bool):
                provider_model_available = availability
                provider_model_source = "open_webui_model_catalog"
            if availability is True:
                observed_model = baseline.model_name
                model_source = "open_webui_model_catalog"
            context_value = backend.get("model_context_window")
            if isinstance(context_value, int) and not isinstance(context_value, bool):
                observed_context = context_value
                source_value = backend.get("model_context_source")
                context_source = (
                    source_value if isinstance(source_value, str) else "open_webui_model_catalog"
                )
                context_kind = "model_capability"

    elif status is not None and subject.infrastructure_subject == "agent-zero":
        observed = status.get("observed")
        live = observed.get("live_application") if isinstance(observed, dict) else None
        if isinstance(live, dict) and not live.get("error"):
            model_value = live.get("observed_model")
            context_value = live.get("observed_context_window")
            utility_model = live.get("observed_utility_model")
            utility_context = live.get("observed_utility_context_window")
            if isinstance(model_value, str) and model_value:
                observed_model = model_value
                model_source = "agent_zero_model_config_api"
            if isinstance(context_value, int) and not isinstance(context_value, bool):
                observed_context = context_value
                context_source = "agent_zero_model_config_api:chat_model.ctx_length"
                context_kind = "configured_effective"
            if isinstance(utility_model, str) and utility_model:
                observed_utility_model = utility_model
            if isinstance(utility_context, int) and not isinstance(utility_context, bool):
                observed_utility_context = utility_context
            availability = live.get("model_available")
            if isinstance(availability, bool):
                provider_model_available = availability
                provider_model_source = "agent_zero_model_search"

    model_matches = (
        None if observed_model is None else observed_model == baseline.model_name
    )
    context_meets = (
        None
        if observed_context is None
        else observed_context >= baseline.minimum_context_window
    )
    context_matches = (
        None
        if requested is None or observed_context is None
        else observed_context == requested
    )
    return cohort_llm_observation(
        expected_baseline_id=subject.llm_baseline_id,
        deployed_baseline_id=deployed_baseline_id,
        model_name=baseline.model_name,
        logical_provider=baseline.logical_provider,
        minimum_context_window=baseline.minimum_context_window,
        requested_context_window=requested,
        observed_model_name=observed_model,
        observed_context_window=observed_context,
        model_observation_source=model_source,
        context_observation_source=context_source,
        context_observation_kind=context_kind,
        model_matches_baseline=model_matches,
        context_meets_minimum=context_meets,
        context_matches_requested=context_matches,
        observed_utility_model=observed_utility_model,
        observed_utility_context_window=observed_utility_context,
        provider_model_available=provider_model_available,
        provider_model_availability_source=provider_model_source,
    )


def summarize_context(
    statuses: Iterable[cohort_subject_status],
) -> cohort_context_summary:
    items = tuple(statuses)
    experiment_ids = {item.experiment_id for item in items if item.experiment_id is not None}
    missing = any(item.experiment_id is None for item in items)
    if not experiment_ids:
        return cohort_context_summary(state="baseline", experiment_id=None)
    if len(experiment_ids) > 1:
        return cohort_context_summary(state="divergent", experiment_id=None)

    experiment_id = next(iter(experiment_ids))
    if missing:
        return cohort_context_summary(state="partial", experiment_id=experiment_id)

    package_values = tuple(item.package_sha256 for item in items)
    package_sha256s = {value for value in package_values if value is not None}
    if package_sha256s:
        if len(package_sha256s) > 1 or any(value is None for value in package_values):
            return cohort_context_summary(
                state="divergent",
                experiment_id=experiment_id,
            )
        return cohort_context_summary(
            state="converged",
            experiment_id=experiment_id,
            package_sha256=next(iter(package_sha256s)),
        )

    return cohort_context_summary(state="converged", experiment_id=experiment_id)


def stop_cohort(
    repo_root: str | Path,
    runtime_root: str | Path,
    subjects: tuple[cohort_subject, ...],
) -> tuple[cohort_stop_result, ...]:
    results: list[cohort_stop_result] = []
    for subject in subjects:
        try:
            lease = discover_runtime_lease(
                repo_root,
                runtime_root,
                subject.infrastructure_subject,
                subject.llm_baseline_id,
            )
            initial_running = lease.initially_running
            if initial_running:
                lease.context.manager.down()
            results.append(
                cohort_stop_result(
                    subject_id=subject.subject_id,
                    infrastructure_subject=subject.infrastructure_subject,
                    initial_running=initial_running,
                    stopped=initial_running,
                )
            )
        except (infrastructure_error, profile_error, docker_error, OSError, ValueError) as exc:
            results.append(
                cohort_stop_result(
                    subject_id=subject.subject_id,
                    infrastructure_subject=subject.infrastructure_subject,
                    initial_running=False,
                    stopped=False,
                    error=str(exc),
                )
            )
    return tuple(results)


def doctor_cohort(
    repo_root: str | Path,
    runtime_root: str | Path,
    subjects: tuple[cohort_subject, ...],
    *,
    start_if_needed: bool = False,
    startup_timeout_seconds: float = 120.0,
    progress: Callable[[int, int, cohort_subject, str], None] | None = None,
) -> tuple[cohort_doctor_result, ...]:
    results: list[cohort_doctor_result] = []
    total = len(subjects)
    for index, subject in enumerate(subjects, start=1):
        lease = None
        initial_running: bool | None = None
        started_by_acmm = False
        live_llm: cohort_llm_observation | None = None
        release_error = None
        if progress is not None:
            progress(index, total, subject, "begin")
        try:
            lease = discover_runtime_lease(
                repo_root,
                runtime_root,
                subject.infrastructure_subject,
                subject.llm_baseline_id,
            )
            initial_running = lease.initially_running
            if progress is not None:
                progress(
                    index,
                    total,
                    subject,
                    "runtime-running" if initial_running else "runtime-stopped",
                )
            if start_if_needed and not initial_running:
                if progress is not None:
                    progress(index, total, subject, "starting")
                lease.ensure_started(timeout_seconds=startup_timeout_seconds)
                started_by_acmm = True
                if progress is not None:
                    progress(index, total, subject, "started")
            if progress is not None:
                progress(index, total, subject, "checking")
            doctor = doctor_deployed_subject(
                repo_root,
                runtime_root,
                subject.infrastructure_subject,
                subject.llm_baseline_id,
            )
            live_status = inspect_subject_context(
                repo_root,
                runtime_root,
                subject,
                include_running=True,
            )
            live_llm = live_status.llm
        except (infrastructure_error, profile_error, docker_error, OSError, ValueError) as exc:
            doctor = deployed_subject_doctor_result(
                infrastructure_subject=subject.infrastructure_subject,
                experiment_id=None,
                report=None,
                error=str(exc),
            )
        finally:
            if lease is not None and lease.started_by_acmm:
                if progress is not None:
                    progress(index, total, subject, "restoring")
                try:
                    lease.release()
                    if progress is not None:
                        progress(index, total, subject, "restored")
                except (
                    infrastructure_error,
                    profile_error,
                    docker_error,
                    OSError,
                    ValueError,
                ) as exc:
                    release_error = str(exc)
                    if progress is not None:
                        progress(index, total, subject, "restore-failed")

        state = deployed_experiment_state(
            repo_root,
            runtime_root,
            subject.infrastructure_subject,
            subject.llm_baseline_id,
        )
        item = cohort_doctor_result(
            subject=subject,
            doctor=doctor,
            deployment_state=state,
            initially_running=initial_running,
            started_by_acmm=started_by_acmm,
            llm=live_llm,
            release_error=release_error,
        )
        results.append(item)
        if progress is not None:
            progress(
                index,
                total,
                subject,
                "complete-ready" if item.is_ready else "complete-not-ready",
            )
    return tuple(results)


def _require_contract_llm_binding(
    repo_root: Path,
    subject: dict[str, object],
) -> None:
    subject_id = subject.get("id")
    if not isinstance(subject_id, str):
        raise infrastructure_error("Contract subject is missing a valid id.")
    binding = lookup_subject_execution_binding(subject_id)
    if binding is None:
        raise infrastructure_error(
            f"Subject '{subject_id}' has no registered execution binding."
        )
    baseline = load_llm_baseline(repo_root, binding.llm_baseline_id)
    model = subject.get("model")
    if not isinstance(model, dict):
        raise infrastructure_error(
            f"Contract subject '{subject_id}' is missing model metadata."
        )
    observed_name = model.get("name")
    observed_provider = model.get("provider")
    if observed_name != baseline.model_name:
        raise infrastructure_error(
            f"Contract subject '{subject_id}' declares model '{observed_name}', but "
            f"its registered LLM baseline '{baseline.id}' requires "
            f"'{baseline.model_name}'."
        )
    if observed_provider != baseline.logical_provider:
        raise infrastructure_error(
            f"Contract subject '{subject_id}' declares provider "
            f"'{observed_provider}', but its registered LLM baseline "
            f"'{baseline.id}' requires '{baseline.logical_provider}'."
        )

    configuration = subject.get("configuration")
    if not isinstance(configuration, dict):
        return
    context_window = configuration.get("context_window")
    if context_window is None:
        return
    if (
        not isinstance(context_window, int)
        or isinstance(context_window, bool)
        or context_window < baseline.minimum_context_window
    ):
        raise infrastructure_error(
            f"Contract subject '{subject_id}' context_window must be an integer at "
            f"least {baseline.minimum_context_window} for LLM baseline "
            f"'{baseline.id}'."
        )


_INFRASTRUCTURE_SUBJECTS = ("hermes", "open-webui", "agent-zero")
_QUALIFICATION_SUBJECT_IDS = {
    "hermes": "hermes_llm_qualification",
    "open-webui": "open_webui_llm_qualification",
    "agent-zero": "agent_zero_llm_qualification",
}


def _deployed_subject_id(
    baseline_id: str,
    infrastructure_subject: str,
) -> str:
    matches: list[str] = []
    for subject_id in registered_subject_ids(baseline_id):
        binding = lookup_subject_execution_binding(subject_id)
        if (
            binding is not None
            and binding.infrastructure_subject == infrastructure_subject
        ):
            matches.append(subject_id)
    if len(matches) > 1:
        raise infrastructure_error(
            f"LLM baseline '{baseline_id}' has multiple subjects for "
            f"infrastructure '{infrastructure_subject}'."
        )
    if matches:
        return matches[0]
    return _QUALIFICATION_SUBJECT_IDS[infrastructure_subject]


def _package_cohort_subjects(
    contract_subjects: object,
    infrastructure_subject: str,
) -> tuple[cohort_subject, ...]:
    if not isinstance(contract_subjects, list):
        raise infrastructure_error("Experiment Package Contract subjects must be a list.")

    matches: list[cohort_subject] = []
    for subject in contract_subjects:
        subject_id = subject.get("id") if isinstance(subject, dict) else None
        if not isinstance(subject_id, str):
            continue
        binding = lookup_subject_execution_binding(subject_id)
        if binding is None:
            raise infrastructure_error(
                f"Subject '{subject_id}' has no registered execution binding."
            )
        if binding.infrastructure_subject != infrastructure_subject:
            continue
        matches.append(
            cohort_subject(
                subject_id=subject_id,
                infrastructure_subject=binding.infrastructure_subject,
                llm_baseline_id=binding.llm_baseline_id,
            )
        )
    return tuple(matches)


def _resolve_subjects(
    available_subject_ids: Iterable[str],
    selected_subject_ids: Iterable[str],
) -> tuple[cohort_subject, ...]:
    available = tuple(available_subject_ids)
    selected = tuple(dict.fromkeys(selected_subject_ids))
    if selected:
        unknown = tuple(item for item in selected if item not in available)
        if unknown:
            raise infrastructure_error(
                "Selected subject(s) are not covered by this cohort: "
                + ", ".join(unknown)
            )
        ordered_ids = tuple(item for item in available if item in selected)
    else:
        ordered_ids = available

    subjects: list[cohort_subject] = []
    used_infrastructure: dict[str, str] = {}
    for subject_id in ordered_ids:
        binding = lookup_subject_execution_binding(subject_id)
        if binding is None:
            raise infrastructure_error(
                f"Subject '{subject_id}' has no registered execution binding."
            )
        previous = used_infrastructure.get(binding.infrastructure_subject)
        if previous is not None:
            raise infrastructure_error(
                f"Subjects '{previous}' and '{subject_id}' share infrastructure "
                f"backend '{binding.infrastructure_subject}', which cannot be "
                "prepared twice in one cohort."
            )
        used_infrastructure[binding.infrastructure_subject] = subject_id
        subjects.append(
            cohort_subject(
                subject_id=subject_id,
                infrastructure_subject=binding.infrastructure_subject,
                llm_baseline_id=binding.llm_baseline_id,
            )
        )
    return tuple(subjects)


def _resolve_qualification_subjects(
    baseline_id: str,
    selected_subject_ids: Iterable[str],
) -> tuple[cohort_subject, ...]:
    available = tuple(
        (_QUALIFICATION_SUBJECT_IDS[item], item)
        for item in _INFRASTRUCTURE_SUBJECTS
    )
    selected = tuple(dict.fromkeys(selected_subject_ids))
    available_ids = tuple(subject_id for subject_id, _ in available)
    if selected:
        unknown = tuple(item for item in selected if item not in available_ids)
        if unknown:
            raise infrastructure_error(
                "Selected subject(s) are not covered by this LLM qualification "
                "cohort: " + ", ".join(unknown)
            )
        available = tuple(item for item in available if item[0] in selected)
    return tuple(
        cohort_subject(
            subject_id=subject_id,
            infrastructure_subject=infrastructure_subject,
            llm_baseline_id=baseline_id,
        )
        for subject_id, infrastructure_subject in available
    )


def _apparatus_state(
    repo_root: str | Path,
    infrastructure_subject: str,
    experiment_id: str | None,
    observed_sha256: str | None,
    *,
    package_expected_sha256: str | None = None,
    package_managed: bool = False,
) -> str:
    if package_managed:
        return (
            "match"
            if observed_sha256 == package_expected_sha256
            else "mismatch"
        )
    if experiment_id is None or observed_sha256 is None:
        return "none"
    try:
        expected = experiment_apparatus_sha256(
            repo_root,
            infrastructure_subject,
            experiment_id,
        )
    except (profile_error, OSError, ValueError):
        return "unavailable"
    return "match" if observed_sha256 == expected else "mismatch"


def _require_applied_context(
    observed: cohort_subject_status,
    experiment_id: str | None,
) -> None:
    if observed.error is not None:
        raise infrastructure_error(
            f"Applied state for '{observed.subject_id}' could not be verified: "
            f"{observed.error}"
        )
    if experiment_id is None:
        if observed.experiment_id is not None:
            raise infrastructure_error(
                f"Baseline apply for '{observed.subject_id}' left experiment "
                f"'{observed.experiment_id}' active."
            )
        return
    if observed.experiment_id != experiment_id:
        raise infrastructure_error(
            f"Applied state for '{observed.subject_id}' reports experiment "
            f"'{observed.experiment_id}', expected '{experiment_id}'."
        )
    if observed.application_scope != "experiment":
        raise infrastructure_error(
            f"Applied state for '{observed.subject_id}' has application scope "
            f"'{observed.application_scope}', expected 'experiment'."
        )
    if observed.apparatus_state != "match":
        raise infrastructure_error(
            f"Applied state for '{observed.subject_id}' has apparatus state "
            f"'{observed.apparatus_state}', expected 'match'."
        )
