from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from acmm.contract.loader import load_contract
from acmm.experiments.catalog import lookup_experiment_execution_binding
from acmm.subjects.catalog import (
    lookup_subject_execution_binding,
    registered_subject_ids,
)
from acmm_infra.common import infrastructure_error
from acmm_infra.deployment import read_experiment_apparatus_state
from acmm_infra.docker import docker_error
from acmm_infra.profiles.common import profile_error
from acmm_infra.profiles.experiments import experiment_apparatus_sha256
from acmm_infra.readiness import doctor_deployed_subject
from acmm_infra.subject_managers import (
    build_deployed_subject_manager,
    build_subject_manager,
    deployed_experiment_state,
)


@dataclass(frozen=True)
class cohort_subject:
    subject_id: str
    infrastructure_subject: str


@dataclass(frozen=True)
class experiment_cohort:
    experiment_id: str | None
    canonical_contract: Path | None
    subjects: tuple[cohort_subject, ...]


@dataclass(frozen=True)
class cohort_subject_status:
    subject_id: str
    infrastructure_subject: str
    running: bool | None
    experiment_id: str | None
    application_scope: str | None
    apparatus_state: str
    source_sha256: str | None
    error: str | None = None


@dataclass(frozen=True)
class cohort_context_summary:
    state: str
    experiment_id: str | None


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

    subject_ids = tuple(item["id"] for item in contract["subjects"])
    subjects = _resolve_subjects(subject_ids, selected_subject_ids)
    return experiment_cohort(
        experiment_id=experiment_id,
        canonical_contract=contract_path,
        subjects=subjects,
    )


def resolve_baseline_cohort(
    selected_subject_ids: Iterable[str] = (),
) -> experiment_cohort:
    subjects = _resolve_subjects(registered_subject_ids(), selected_subject_ids)
    return experiment_cohort(
        experiment_id=None,
        canonical_contract=None,
        subjects=subjects,
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
        )
        status = context.manager.status() if include_running else None
        marker = read_experiment_apparatus_state(context.manager.deployment_root)
        experiment_id = context.experiment_id
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
            error=str(exc),
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
    return cohort_context_summary(state="converged", experiment_id=experiment_id)


def doctor_cohort(
    repo_root: str | Path,
    runtime_root: str | Path,
    subjects: tuple[cohort_subject, ...],
):
    return tuple(
        (
            subject,
            doctor_deployed_subject(
                repo_root,
                runtime_root,
                subject.infrastructure_subject,
            ),
            deployed_experiment_state(
                repo_root,
                runtime_root,
                subject.infrastructure_subject,
            ),
        )
        for subject in subjects
    )


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
            )
        )
    return tuple(subjects)


def _apparatus_state(
    repo_root: str | Path,
    infrastructure_subject: str,
    experiment_id: str | None,
    observed_sha256: str | None,
) -> str:
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
