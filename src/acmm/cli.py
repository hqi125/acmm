from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys
import time
from typing import Any, Mapping

from acmm.analysis.export import analysis_export_error, export_execution
from acmm.contract.compatibility import validate_harness_compatibility
from acmm.contract.loader import contract_load_error, load_contract
from acmm.contract.validator import (
    load_schema,
    load_schema_for_contract,
    validate_contract,
)
from acmm.preflight.deployment import (
    deployment_preflight_result,
    inspect_package_deployment_contexts,
)
from acmm.experiment_package import (
    acquire_staged_integration_resources,
    apply_staged_integration_resources,
    compose_execution_registry,
    compose_staged_subject_admission_controller,
    doctor_staged_integration_resources,
    experiment_package,
    experiment_package_error,
    experiment_package_store_error,
    experiment_runtime_error,
    load_staged_experiment_runtime,
    open_staged_experiment_package,
    package_provenance,
    restore_staged_integration_resources,
    resolve_installed_experiment_package,
    run_staged_experiment_apparatus_action,
    run_staged_experiment_apparatus_cleanup,
    staged_tool_result_error_interpreters,
    validate_staged_package_secrets,
)
from acmm.experiment_package.cli import register_experiment_package_commands
from acmm.environment_cli import register_environment_commands
from acmm.generated_paths import default_exports_root, default_results_root
from acmm.campaign.cli import register_campaign_commands
from acmm.secret_bindings import secret_binding_error
from acmm.secret_bindings.cli import register_secret_commands
from acmm.preflight.live import (
    inspect_live_readiness,
    live_readiness_preflight_result,
)
from acmm.subjects.catalog import (
    lookup_subject_execution_binding,
    subject_execution_binding,
    unsupported_termination_controls,
)
from acmm.record.loader import load_run_record, run_record_load_error
from acmm.record.validator import (
    default_run_record_schema_path,
    load_run_record_schema,
    sha256_file,
    validate_run_record,
)
from acmm.runtime.diagnostics import (
    capture_subject_runtime_log,
    runtime_log_capture_error,
)
from acmm.runtime.experiment_execution import (
    execute_experiment,
    experiment_execution_interrupted,
    experiment_execution_result,
    experiment_interruption_result,
)
from acmm.runtime.execution_summary import (
    execution_provider_usage_summary,
    format_execution_duration,
    format_execution_timestamp,
    write_execution_summary,
    write_interruption_summary,
)
from acmm.runtime.provider_usage import (
    OLLAMA_CLOUD_API_KEY_SECRET_ID,
    ollama_cloud_usage_monitor,
    resolve_ollama_cloud_api_key,
)
from acmm.runtime.planner import (
    build_experiment_plan,
    build_materialization_slices,
    experiment_plan,
    planned_run,
    select_planned_runs,
    selected_subject_ids as selected_subject_ids_for_runs,
)
from acmm.runtime.result_store import result_write_error
from acmm.campaign.execution import (
    campaign_execution_context,
    finalize_campaign_execution,
    finalize_campaign_execution_failure,
    frozen_campaign_materialization_group_by,
    initialize_campaign_execution,
    load_frozen_campaign_contract,
)
from acmm.validation import validation_result
from acmm_infra.cohort import (
    apply_cohort,
    inspect_cohort,
    resolve_deployed_cohort,
    resolve_package_cohort,
    summarize_context,
)
from acmm_infra.common import infrastructure_error
from acmm_infra.docker import docker_error
from acmm_infra.profiles.llm import (
    load_llm_baseline,
    runtime_provenance_configuration,
)
from acmm_infra.profiles.common import (
    default_repo_root,
    default_runtime_root,
    profile_error,
)
from acmm_infra.runtime_lifecycle import (
    discover_runtime_lease,
    subject_runtime_lease,
)


class _run_help_formatter(argparse.HelpFormatter):
    def _format_action(self, action: argparse.Action) -> str:
        rendered = super()._format_action(action)
        if action.dest != "experiment":
            return rendered

        apparatus_entry = argparse.Action(
            option_strings=[],
            dest="apparatus",
            nargs=None,
            help="Inspect or operate engineering apparatus commands.",
        )
        return rendered + super()._format_action(apparatus_entry)


def _build_run_apparatus_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acmm run apparatus",
        description=(
            "Inspect or operate engineering apparatus for the active, fully "
            "converged Experiment Package deployment."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="apparatus_command",
        metavar="COMMAND",
        required=True,
    )

    list_parser = subparsers.add_parser(
        "list",
        help="List typed apparatus capabilities and package-owned free-form actions.",
    )
    list_parser.set_defaults(handler=_run_apparatus_list)

    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="Clean quarantined external resources recorded by one execution.",
    )
    cleanup_parser.add_argument(
        "--execution",
        required=True,
        metavar="PATH",
        help="Execution directory or experiment_execution.yaml path to clean.",
    )
    cleanup_parser.add_argument(
        "--preview",
        action="store_true",
        help="Verify and list cleanup targets without deleting them.",
    )
    cleanup_parser.set_defaults(handler=_run_apparatus_cleanup)

    exec_parser = subparsers.add_parser(
        "exec",
        help="Execute one package-owned free-form apparatus action.",
    )
    exec_parser.add_argument(
        "action",
        metavar="ACTION",
        help="Package-owned apparatus action identifier.",
    )
    exec_parser.set_defaults(handler=_run_apparatus_exec)
    return parser


def _parse_cli_arguments(
    argv: list[str] | None = None,
    namespace: argparse.Namespace | None = None,
) -> argparse.Namespace:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) >= 2 and values[0] == "run" and values[1] == "apparatus":
        return _build_run_apparatus_parser().parse_args(values[2:], namespace)
    return _build_parser().parse_args(values, namespace)

def _harness_version() -> str:
    try:
        return version("acmm")
    except PackageNotFoundError:
        return "0.1.0"


def _execution_package_provenance(
    package: experiment_package,
) -> dict[str, str | None]:
    provenance = package_provenance(package)
    return {
        "schema_version": provenance.package_schema_version,
        "sha256": provenance.package_sha256,
        "contract_sha256": provenance.contract_sha256,
        "runtime_sha256": provenance.runtime_sha256,
    }


def _print_issue(prefix: str, path: str, code: str, message: str) -> None:
    print(f"{prefix}: {path}: [{code}] {message}")


def _print_validation_result(result: validation_result) -> None:
    for issue in result.errors:
        _print_issue("ERROR", issue.path, issue.code, issue.message)

    for issue in result.warnings:
        _print_issue("WARNING", issue.path, issue.code, issue.message)


def _load_and_validate_contract(
    contract_path: Path,
    schema_path: Path | None,
) -> tuple[dict[str, Any] | None, validation_result | None, int]:
    try:
        contract = load_contract(contract_path)
    except contract_load_error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return None, None, 2

    try:
        schema = load_schema_for_contract(contract, schema_path)
    except (OSError, ValueError, result_write_error) as exc:
        source = str(schema_path) if schema_path is not None else "bundled schema selected by Contract"
        print(
            f"ERROR: Could not load Contract schema ({source}): {exc}",
            file=sys.stderr,
        )
        return None, None, 2

    result = validate_contract(contract, schema)
    _print_validation_result(result)

    if not result.is_valid:
        print(
            f"Contract validation failed with {len(result.errors)} error(s) "
            f"and {len(result.warnings)} warning(s)."
        )
        return contract, result, 1

    return contract, result, 0



def _compact_integer_ranges(values: tuple[int, ...] | list[int]) -> str:
    unique = sorted(set(values))

    if not unique:
        return "none"

    groups: list[str] = []
    start = unique[0]
    end = start

    for value in unique[1:]:
        if value == end + 1:
            end = value
            continue

        groups.append(str(start) if start == end else f"{start}-{end}")
        start = value
        end = value

    groups.append(str(start) if start == end else f"{start}-{end}")
    return ", ".join(groups)


def _print_labeled_fields(
    fields: tuple[tuple[str, object], ...] | list[tuple[str, object]],
    *,
    indent: int,
) -> None:
    if not fields:
        return

    label_width = max(len(label) for label, _ in fields)
    prefix = " " * indent

    for label, value in fields:
        print(f"{prefix}{label + ':':<{label_width + 1}} {value}")


def _print_subject_runtime_details(
    runtime_details: list[tuple[str, tuple[tuple[str, str], ...]]],
) -> None:
    print("Execution subject runtime:")

    for subject_id, details in runtime_details:
        print(f"  {subject_id}:")
        _print_labeled_fields(details, indent=4)


def _print_deployment_preflight(
    result: deployment_preflight_result,
    *,
    warning_only: bool,
    title: str = "Deployment context preflight",
) -> None:
    print(f"{title}:")

    for subject in result.subjects:
        experiment = subject.experiment_id or "none"
        context = "ready" if subject.is_ready else "not-ready"
        print(f"  {subject.subject_id}:")
        _print_labeled_fields(
            [
                ("context", context),
                ("experiment", experiment),
                ("package SHA-256", subject.package_sha256 or "none"),
            ],
            indent=4,
        )

        if subject.is_ready:
            continue

        severity = "WARNING" if warning_only else "ERROR"
        for issue in subject.issues:
            print(f"{severity}: {subject.subject_id}: {issue}", file=sys.stderr)


def _print_live_readiness_preflight(
    result: live_readiness_preflight_result,
) -> None:
    print("Live readiness preflight:")

    for subject in result.subjects:
        readiness = "ready" if subject.is_ready else "not-ready"
        print(f"  {subject.subject_id}:")
        _print_labeled_fields([("readiness", readiness)], indent=4)

        if subject.error is not None:
            print(
                f"ERROR: {subject.subject_id}: {subject.error}",
                file=sys.stderr,
            )

        for check in subject.failed_checks:
            print(
                f"ERROR: {subject.subject_id}: [{check.name}] {check.detail}",
                file=sys.stderr,
            )



def _print_experiment_execution_selection(
    *,
    plan: experiment_plan,
    selected_runs: tuple[planned_run, ...],
    selected_indices: tuple[int, ...],
) -> None:
    if len(selected_runs) == plan.run_count:
        print("Experiment execution:")
        _print_labeled_fields(
            [
                ("experiment", plan.experiment_id),
                ("selection", f"all {plan.run_count} planned runs"),
            ],
            indent=2,
        )
    elif len(selected_runs) == 1:
        planned = selected_runs[0]
        print("Experiment execution:")
        _print_labeled_fields(
            [
                ("run", f"{planned.run_index}/{plan.run_count - 1}"),
                ("subject", planned.subject_id),
                ("scenario", planned.scenario_id),
                ("repetition", planned.repetition),
            ],
            indent=2,
        )
    else:
        print("Experiment execution:")
        _print_labeled_fields(
            [
                ("experiment", plan.experiment_id),
                (
                    "selection",
                    f"{len(selected_runs)} of {plan.run_count} planned runs",
                ),
                ("run indices", _compact_integer_ranges(selected_indices)),
            ],
            indent=2,
        )

def _discover_runtime_leases(
    *,
    repo_root: Path,
    runtime_root: Path,
    subject_bindings: dict[str, subject_execution_binding],
) -> dict[str, subject_runtime_lease]:
    return {
        subject_id: discover_runtime_lease(
            repo_root,
            runtime_root,
            binding.infrastructure_subject,
        )
        for subject_id, binding in subject_bindings.items()
    }


def _print_start_if_needed_preflight(
    leases: dict[str, subject_runtime_lease],
    running_readiness: live_readiness_preflight_result,
) -> None:
    by_subject = {item.subject_id: item for item in running_readiness.subjects}
    print("Live readiness preflight:")
    for subject_id, lease in leases.items():
        print(f"  {subject_id}:")
        if lease.initially_running:
            subject = by_subject[subject_id]
            readiness = "ready" if subject.is_ready else "not-ready"
            _print_labeled_fields(
                [("readiness", readiness), ("runtime", "already running")],
                indent=4,
            )
            if subject.error is not None:
                print(f"ERROR: {subject_id}: {subject.error}", file=sys.stderr)
            for check in subject.failed_checks:
                print(
                    f"ERROR: {subject_id}: [{check.name}] {check.detail}",
                    file=sys.stderr,
                )
        else:
            _print_labeled_fields(
                [
                    ("readiness", "deferred"),
                    ("runtime", "stopped; start permission granted"),
                ],
                indent=4,
            )


_STARTUP_RETRY_DELAYS_SECONDS = (1.0, 5.0, 15.0, 30.0, 60.0)


def _startup_retry_delay_seconds(failed_attempt: int) -> float:
    index = min(
        max(failed_attempt - 1, 0),
        len(_STARTUP_RETRY_DELAYS_SECONDS) - 1,
    )
    return _STARTUP_RETRY_DELAYS_SECONDS[index]


def _ensure_runtime_ready(
    *,
    subject_id: str,
    lease: subject_runtime_lease,
    binding: subject_execution_binding,
    repo_root: Path,
    runtime_root: Path,
    startup_timeout_seconds: float,
    startup_max_attempts: int,
    heading: str | None = "Runtime acquisition",
) -> None:
    if heading is not None:
        print(f"{heading}:")
    print(f"  {subject_id}:")

    for attempt in range(1, startup_max_attempts + 1):
        _print_labeled_fields(
            [("startup attempt", f"{attempt}/{startup_max_attempts}")],
            indent=4,
        )

        try:
            if lease.is_running:
                _print_labeled_fields(
                    [("action", "verifying running runtime")],
                    indent=4,
                )
            else:
                _print_labeled_fields([("action", "starting")], indent=4)
                lease.ensure_started(timeout_seconds=startup_timeout_seconds)

            readiness = inspect_live_readiness(
                repo_root=repo_root,
                runtime_root=runtime_root,
                subject_bindings={subject_id: binding},
            )
            subject = readiness.subjects[0]
            action = (
                "started by ACMM"
                if lease.started_by_acmm
                else "already running"
            )
            _print_labeled_fields(
                [
                    ("action", action),
                    (
                        "readiness",
                        "ready" if subject.is_ready else "not-ready",
                    ),
                ],
                indent=4,
            )
            if subject.is_ready:
                return

            if subject.error is not None:
                print(
                    f"ERROR: {subject_id}: {subject.error}",
                    file=sys.stderr,
                )
            for check in subject.failed_checks:
                print(
                    f"ERROR: {subject_id}: [{check.name}] {check.detail}",
                    file=sys.stderr,
                )
            raise infrastructure_error(
                f"Subject '{subject_id}' did not become live-ready after startup."
            )
        except (
            infrastructure_error,
            profile_error,
            docker_error,
            OSError,
            ValueError,
        ) as exc:
            if attempt >= startup_max_attempts:
                raise

            if lease.started_by_acmm:
                try:
                    lease.release()
                except (
                    infrastructure_error,
                    profile_error,
                    docker_error,
                    OSError,
                    ValueError,
                ) as cleanup_exc:
                    raise infrastructure_error(
                        f"Could not reset failed runtime startup for "
                        f"'{subject_id}': {cleanup_exc}"
                    ) from cleanup_exc

            delay = _startup_retry_delay_seconds(attempt)
            print(
                f"WARNING: {subject_id}: startup attempt "
                f"{attempt}/{startup_max_attempts} failed: {exc}",
                file=sys.stderr,
            )
            _print_labeled_fields(
                [
                    (
                        "retry",
                        f"waiting {delay:g}s before startup attempt "
                        f"{attempt + 1}/{startup_max_attempts}",
                    )
                ],
                indent=4,
            )
            time.sleep(delay)


def _release_runtime_leases(
    leases: dict[str, subject_runtime_lease],
) -> tuple[str, ...]:
    errors: list[str] = []
    for subject_id, lease in leases.items():
        if not lease.started_by_acmm:
            continue
        try:
            lease.release()
            print("Runtime release:")
            print(f"  {subject_id}:")
            _print_labeled_fields([("result", "stopped")], indent=4)
        except (infrastructure_error, profile_error, docker_error, OSError, ValueError) as exc:
            errors.append(f"{subject_id}: {exc}")
    return tuple(errors)


def _print_run_selection_preview(
    plan: experiment_plan,
    selected_runs: tuple[planned_run, ...],
    runtime_details: list[tuple[str, tuple[tuple[str, str], ...]]],
) -> None:
    subject_ids = selected_subject_ids_for_runs(selected_runs)
    run_indices = tuple(run.run_index for run in selected_runs)
    grouped: dict[tuple[str, str], list[planned_run]] = {}

    for run in selected_runs:
        grouped.setdefault((run.subject_id, run.scenario_id), []).append(run)

    print("Run selection preview:")
    print(f"  experiment:    {plan.experiment_id}")
    print(f"  contract runs: {plan.run_count}")
    print(f"  selected:      {len(selected_runs)}")
    print(f"  subjects:      {', '.join(subject_ids)}")
    print(f"  run indices:   {_compact_integer_ranges(run_indices)}")
    print("  breakdown:")

    for (subject_id, scenario_id), runs in grouped.items():
        indices = tuple(run.run_index for run in runs)
        repetitions = tuple(run.repetition for run in runs)
        print(f"    {subject_id} / {scenario_id}:")
        _print_labeled_fields(
            [
                ("runs", _compact_integer_ranges(indices)),
                ("repetitions", _compact_integer_ranges(repetitions)),
                ("count", len(runs)),
            ],
            indent=6,
        )

    _print_subject_runtime_details(runtime_details)
    print("No execution started (--preview).")

def _resolve_run_package(
    selector: str,
    runtime_root: Path,
    schema_path: Path | None,
) -> experiment_package:
    try:
        package = resolve_installed_experiment_package(
            runtime_root,
            selector,
            contract_schema_path=schema_path,
        )
    except (experiment_package_store_error, experiment_package_error, OSError, ValueError) as exc:
        raise ValueError(str(exc)) from exc

    print("Experiment resolution:")
    _print_labeled_fields(
        [
            ("source", "installed Experiment Package"),
            ("selector", selector),
            ("experiment", package.experiment_id),
            ("package SHA-256", package.package_sha256),
            ("installed package", package.root),
            ("Contract", package.contract_path),
        ],
        indent=2,
    )
    return package


def _apply_selected_package_context(
    *,
    repo_root: Path,
    runtime_root: Path,
    package: experiment_package,
    selected_subject_ids: tuple[str, ...],
) -> None:
    cohort = resolve_package_cohort(
        repo_root,
        package,
        selected_subject_ids,
    )

    def resource_progress(integration_id, message) -> None:
        print(f"[integration:{integration_id}] {message}", flush=True)

    validate_staged_package_secrets(package)
    apply_staged_integration_resources(
        package,
        runtime_root,
        timeout_seconds=120.0,
        progress=resource_progress,
    )

    def progress(index, total, subject, payload) -> None:
        if index is not None:
            print()
            print(f"[apply {index}/{total}] {subject.subject_id}")
            print(f"  infrastructure: {subject.infrastructure_subject}")
            print("  provisioning:")
            return
        print(f"    {payload}", flush=True)

    apply_cohort(
        repo_root,
        runtime_root,
        cohort,
        timeout_seconds=120.0,
        progress=progress,
    )


def _resolve_active_run_package() -> experiment_package:
    repo_root = default_repo_root().expanduser().resolve()
    runtime_root = default_runtime_root().expanduser().resolve()
    cohort = resolve_deployed_cohort(repo_root, runtime_root)
    statuses = inspect_cohort(repo_root, runtime_root, cohort.subjects)
    context = summarize_context(statuses)
    if (
        context.state != "converged"
        or not context.experiment_id
        or not context.package_sha256
    ):
        raise ValueError(
            "acmm run apparatus requires one fully converged Experiment Package "
            "deployment context."
        )
    return open_staged_experiment_package(
        runtime_root,
        context.experiment_id,
        context.package_sha256,
    )


def _load_active_apparatus():
    package = _resolve_active_run_package()
    validate_staged_package_secrets(package)
    loaded = load_staged_experiment_runtime(package)
    return package, loaded


def _print_apparatus_load_error(exc: Exception) -> int:
    print(f"ERROR: {exc}", file=sys.stderr)
    return 2


def _run_apparatus_list(args: argparse.Namespace) -> int:
    del args
    try:
        package, loaded = _load_active_apparatus()
    except (
        experiment_package_store_error,
        experiment_package_error,
        experiment_runtime_error,
        infrastructure_error,
        profile_error,
        OSError,
        ValueError,
    ) as exc:
        return _print_apparatus_load_error(exc)

    print("Experiment apparatus operations:")
    print(f"  experiment: {package.experiment_id}")
    print(f"  package:    {package.package_sha256}")
    print("  lifecycle:")
    if loaded.runtime.apparatus_cleanup is None:
        print("    cleanup: unavailable")
    else:
        cleanup = loaded.runtime.apparatus_cleanup
        description = getattr(cleanup, "description", None)
        suffix = (
            f" - {description.strip()}"
            if isinstance(description, str) and description.strip()
            else ""
        )
        print(f"    cleanup{suffix}")
    if not loaded.runtime.apparatus_actions:
        print("  actions:    none")
        return 0
    print("  actions:")
    for name, action in sorted(loaded.runtime.apparatus_actions.items()):
        description = getattr(action, "description", None)
        mutation = getattr(action, "mutates_external_state", None)
        suffix_parts = []
        if isinstance(description, str) and description.strip():
            suffix_parts.append(description.strip())
        if isinstance(mutation, bool):
            suffix_parts.append("external mutation" if mutation else "read-only")
        suffix = " - " + "; ".join(suffix_parts) if suffix_parts else ""
        print(f"    {name}{suffix}")
    return 0


def _run_apparatus_cleanup(args: argparse.Namespace) -> int:
    try:
        package, loaded = _load_active_apparatus()
    except (
        experiment_package_store_error,
        experiment_package_error,
        experiment_runtime_error,
        infrastructure_error,
        profile_error,
        OSError,
        ValueError,
    ) as exc:
        return _print_apparatus_load_error(exc)

    if loaded.runtime.apparatus_cleanup is None:
        print(
            f"ERROR: Experiment Package '{package.experiment_id}' does not provide apparatus cleanup.",
            file=sys.stderr,
        )
        return 2

    execution_path = args.execution
    progress_started = False

    def report_cleanup_progress(event) -> None:
        nonlocal progress_started
        if not progress_started:
            print("Experiment apparatus cleanup progress:")
            print(f"  experiment: {package.experiment_id}")
            print(f"  execution:  {Path(execution_path).expanduser()}")
            progress_started = True
        if event.step is not None and event.total is not None:
            prefix = f"[{event.step}/{event.total}]"
        elif event.step is not None:
            prefix = f"[{event.step}]"
        else:
            prefix = "[-]"
        print(f"  {prefix} {event.message}", flush=True)

    try:
        result = run_staged_experiment_apparatus_cleanup(
            package,
            str(Path(execution_path).expanduser().resolve()),
            preview=bool(args.preview),
            progress=report_cleanup_progress,
        )
    except (experiment_runtime_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if progress_started:
        print()
    print("Experiment apparatus cleanup complete:")
    print(f"  experiment: {package.experiment_id}")
    print(f"  package:    {package.package_sha256}")
    print(f"  mode:       {'preview' if args.preview else 'delete'}")
    print(f"  changed:    {'yes' if result.changed else 'no'}")
    print(f"  detail:     {result.detail}")
    if result.observations:
        print("  observations:")
        for key in sorted(result.observations):
            print(f"    {key}: {result.observations[key]}")
    return 0


def _run_apparatus_exec(args: argparse.Namespace) -> int:
    try:
        package, loaded = _load_active_apparatus()
    except (
        experiment_package_store_error,
        experiment_package_error,
        experiment_runtime_error,
        infrastructure_error,
        profile_error,
        OSError,
        ValueError,
    ) as exc:
        return _print_apparatus_load_error(exc)

    action_id = args.action
    if action_id not in loaded.runtime.apparatus_actions:
        print(
            f"ERROR: Experiment Package '{package.experiment_id}' does not provide apparatus action '{action_id}'.",
            file=sys.stderr,
        )
        return 2

    progress_started = False

    def report_progress(event) -> None:
        nonlocal progress_started
        if not progress_started:
            print("Experiment apparatus progress:")
            print(f"  experiment: {package.experiment_id}")
            print(f"  action:     {action_id}")
            progress_started = True
        if event.step is not None and event.total is not None:
            prefix = f"[{event.step}/{event.total}]"
        elif event.step is not None:
            prefix = f"[{event.step}]"
        else:
            prefix = "[-]"
        print(f"  {prefix} {event.message}", flush=True)

    try:
        result = run_staged_experiment_apparatus_action(
            package,
            action_id,
            progress=report_progress,
        )
    except (experiment_runtime_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if progress_started:
        print()
    print("Experiment apparatus action complete:")
    print(f"  experiment: {package.experiment_id}")
    print(f"  package:    {package.package_sha256}")
    print(f"  action:     {action_id}")
    print(f"  changed:    {'yes' if result.changed else 'no'}")
    print(f"  detail:     {result.detail}")
    if result.observations:
        print("  observations:")
        for key in sorted(result.observations):
            print(f"    {key}: {result.observations[key]}")
    return 0


def _run_command(args: argparse.Namespace) -> int:
    return _run_experiment(args)


def _run_experiment(
    args: argparse.Namespace,
    *,
    campaign_context: campaign_execution_context | None = None,
) -> int:
    schema_path = None
    repo_root = default_repo_root().expanduser().resolve()
    runtime_root = default_runtime_root().expanduser().resolve()

    if args.prestart_selected_subjects and not args.start_if_needed:
        print(
            "ERROR: --prestart-selected-subjects requires --start-if-needed.",
            file=sys.stderr,
        )
        return 2
    if args.startup_max_attempts < 1:
        print(
            "ERROR: --startup-max-attempts must be an integer >= 1.",
            file=sys.stderr,
        )
        return 2

    try:
        package = _resolve_run_package(
            args.experiment,
            runtime_root,
            schema_path,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if campaign_context is None:
        contract = dict(package.contract)
        contract_path = package.contract_path
    else:
        if package.package_sha256 != campaign_context.artifact.manifest["package"]["sha256"]:
            print(
                "ERROR: Installed Experiment Package revision does not match the "
                "frozen campaign.",
                file=sys.stderr,
            )
            return 2
        try:
            contract = load_frozen_campaign_contract(campaign_context.artifact)
        except (ValueError, OSError) as exc:
            print(f"ERROR: Could not load frozen campaign Contract: {exc}", file=sys.stderr)
            return 2
        contract_path = (
            campaign_context.artifact.path
            / campaign_context.artifact.manifest["campaign"]["contract_artifact"]
        )

    try:
        loaded_runtime = load_staged_experiment_runtime(package)
    except (experiment_runtime_error, ValueError, OSError) as exc:
        print(f"ERROR: Could not activate Experiment Package runtime: {exc}", file=sys.stderr)
        return 2

    compatibility = validate_harness_compatibility(
        contract,
        loaded_runtime.runtime.capabilities,
    )
    _print_validation_result(compatibility)
    if not compatibility.is_valid:
        print(
            "Harness compatibility validation failed with "
            f"{len(compatibility.errors)} error(s)."
        )
        return 1

    if campaign_context is None:
        plan = build_experiment_plan(contract)

        try:
            selected_runs = select_planned_runs(
                plan,
                contract,
                run_selectors=args.run_index,
                subject_selectors=args.subject,
                subject_run_selectors=args.subject_run_index,
                scenario_selectors=args.scenario,
                repetition_selectors=args.repetition,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    else:
        plan = campaign_context.plan
        selected_runs = campaign_context.selected_runs

    if campaign_context is not None:
        materialization_group_by = frozen_campaign_materialization_group_by(
            campaign_context.artifact
        )
    elif package.campaign_materialization is not None:
        materialization_group_by = package.campaign_materialization.group_by
    else:
        materialization_group_by = ("run_index",)

    subject_order = tuple(subject["id"] for subject in contract["subjects"])
    materialization_slices = build_materialization_slices(
        selected_runs,
        subject_order=subject_order,
        group_by=materialization_group_by,
    )
    selected_runs = tuple(
        run
        for materialization in materialization_slices
        for run in materialization.runs
    )

    selected_indices = tuple(run.run_index for run in selected_runs)
    selected_subject_ids = selected_subject_ids_for_runs(selected_runs)
    selected_subject_set = set(selected_subject_ids)
    subject_bindings = {}
    subject_runtime_details: list[
        tuple[str, tuple[tuple[str, str], ...]]
    ] = []

    for subject in contract["subjects"]:
        subject_id = subject["id"]

        if subject_id not in selected_subject_set:
            continue

        subject_binding = lookup_subject_execution_binding(subject_id)

        if subject_binding is None:
            print(
                "ERROR: No qualified execution binding is registered for subject "
                f"'{subject_id}'.",
                file=sys.stderr,
            )
            return 2

        unsupported = unsupported_termination_controls(
            subject_binding,
            contract["run_protocol"]["termination"],
        )
        if unsupported:
            print(
                "ERROR: Subject "
                f"'{subject_id}' cannot enforce the Contract termination control(s) "
                + ", ".join(unsupported)
                + " exactly for one run.",
                file=sys.stderr,
            )
            return 2

        subject_bindings[subject_id] = subject_binding
        subject_runtime_details.append(
            (subject_id, subject_binding.runtime_details)
        )

    deployment_preflight = inspect_package_deployment_contexts(
        runtime_root=runtime_root,
        package=package,
        subject_bindings=subject_bindings,
    )
    initial_deployment_ready = deployment_preflight.is_ready
    reconciliation_performed = False

    if args.preview:
        _print_deployment_preflight(
            deployment_preflight,
            warning_only=True,
        )
        if not deployment_preflight.is_ready:
            if args.apply_if_needed:
                print(
                    "INFO: Experiment context reconciliation is permitted by "
                    "--apply-if-needed. Shared integration resources and the "
                    "selected subject context would be applied before execution, "
                    "followed by a second strict deployment preflight."
                )
            else:
                print(
                    "WARNING: Experiment context reconciliation is not permitted. "
                    "Execution would fail in strict mode.",
                    file=sys.stderr,
                )
        _print_run_selection_preview(
            plan,
            selected_runs,
            subject_runtime_details,
        )
        return 0

    if not deployment_preflight.is_ready and args.apply_if_needed:
        _print_deployment_preflight(
            deployment_preflight,
            warning_only=True,
        )
        print(
            "INFO: Experiment context is not converged; --apply-if-needed permits "
            "ACMM to reconcile shared integration resources and the selected "
            "subject context before execution."
        )
        try:
            _apply_selected_package_context(
                repo_root=repo_root,
                runtime_root=runtime_root,
                package=package,
                selected_subject_ids=selected_subject_ids,
            )
            reconciliation_performed = True
        except (
            infrastructure_error,
            profile_error,
            docker_error,
            OSError,
            ValueError,
        ) as exc:
            print(
                f"ERROR: Experiment context reconciliation failed: {exc}",
                file=sys.stderr,
            )
            print("ERROR: No execution session was created.", file=sys.stderr)
            return 2

        deployment_preflight = inspect_package_deployment_contexts(
            runtime_root=runtime_root,
            package=package,
            subject_bindings=subject_bindings,
        )
        _print_deployment_preflight(
            deployment_preflight,
            warning_only=False,
            title="Deployment context preflight after reconciliation",
        )
    else:
        _print_deployment_preflight(
            deployment_preflight,
            warning_only=False,
        )

    if not deployment_preflight.is_ready:
        print(
            "ERROR: Deployment context preflight failed; no execution session "
            "was created.",
            file=sys.stderr,
        )
        return 2

    runtime_leases: dict[str, subject_runtime_lease] = {}
    integration_resource_leases = ()
    ready_subject_ids: set[str] = set()

    def cleanup_runtime_resources() -> tuple[str, ...]:
        errors = list(_release_runtime_leases(runtime_leases))
        if integration_resource_leases:
            restore_errors = restore_staged_integration_resources(
                package,
                runtime_root,
                integration_resource_leases,
            )
            errors.extend(
                f"integration {integration_id}: {detail}"
                for integration_id, detail in restore_errors
            )
        return tuple(errors)

    try:
        integration_resource_leases = acquire_staged_integration_resources(
            package,
            runtime_root,
            start_if_needed=bool(args.start_if_needed),
            timeout_seconds=args.startup_timeout_seconds,
            progress=_run_integration_resource_progress,
        )
    except (experiment_package_error, OSError, ValueError) as exc:
        print(
            f"ERROR: Could not acquire integration resources: {exc}",
            file=sys.stderr,
        )
        return 2

    stopped_resources = [
        lease.integration_id
        for lease in integration_resource_leases
        if not lease.initially_running and not lease.started_by_acmm
    ]
    if stopped_resources:
        print(
            "ERROR: Integration resource(s) are stopped: "
            + ", ".join(stopped_resources)
            + "; rerun with --start-if-needed.",
            file=sys.stderr,
        )
        return 2

    try:
        resource_reports = doctor_staged_integration_resources(package, runtime_root)
    except (experiment_package_error, OSError, ValueError) as exc:
        cleanup_errors = cleanup_runtime_resources()
        print(
            f"ERROR: Could not verify integration resources: {exc}",
            file=sys.stderr,
        )
        for error in cleanup_errors:
            print(f"WARNING: Runtime cleanup failed: {error}", file=sys.stderr)
        return 2

    failed_resources = [
        integration_id
        for integration_id, report in resource_reports
        if not report.is_ready
    ]
    if failed_resources:
        cleanup_errors = cleanup_runtime_resources()
        print(
            "ERROR: Integration resource readiness failed: "
            + ", ".join(failed_resources),
            file=sys.stderr,
        )
        for error in cleanup_errors:
            print(f"WARNING: Runtime cleanup failed: {error}", file=sys.stderr)
        return 2

    if args.start_if_needed:
        try:
            runtime_leases = _discover_runtime_leases(
                repo_root=repo_root,
                runtime_root=runtime_root,
                subject_bindings=subject_bindings,
            )
        except (infrastructure_error, profile_error, docker_error, OSError, ValueError) as exc:
            cleanup_errors = cleanup_runtime_resources()
            print(f"ERROR: Could not inspect selected subject runtimes: {exc}", file=sys.stderr)
            for error in cleanup_errors:
                print(f"WARNING: Runtime cleanup failed: {error}", file=sys.stderr)
            return 2

        running_bindings = {
            subject_id: subject_bindings[subject_id]
            for subject_id, lease in runtime_leases.items()
            if lease.initially_running
        }
        live_readiness = inspect_live_readiness(
            repo_root=repo_root,
            runtime_root=runtime_root,
            subject_bindings=running_bindings,
        )
        _print_start_if_needed_preflight(runtime_leases, live_readiness)

        if not live_readiness.is_ready:
            cleanup_errors = cleanup_runtime_resources()
            print(
                "ERROR: Live readiness preflight failed for an already-running "
                "subject; no execution session was created.",
                file=sys.stderr,
            )
            for error in cleanup_errors:
                print(f"WARNING: Runtime cleanup failed: {error}", file=sys.stderr)
            return 2
        ready_subject_ids.update(
            item.subject_id for item in live_readiness.subjects if item.is_ready
        )
    else:
        live_readiness = inspect_live_readiness(
            repo_root=repo_root,
            runtime_root=runtime_root,
            subject_bindings=subject_bindings,
        )
        _print_live_readiness_preflight(live_readiness)

        if not live_readiness.is_ready:
            cleanup_errors = cleanup_runtime_resources()
            print(
                "ERROR: Live readiness preflight failed; no execution session was "
                "created.",
                file=sys.stderr,
            )
            for error in cleanup_errors:
                print(f"WARNING: Runtime cleanup failed: {error}", file=sys.stderr)
            return 2

    if args.capture_runtime_logs and not args.start_if_needed:
        for subject_id, subject_binding in subject_bindings.items():
            try:
                runtime_leases[subject_id] = discover_runtime_lease(
                    repo_root,
                    runtime_root,
                    subject_binding.infrastructure_subject,
                )
            except (
                infrastructure_error,
                profile_error,
                docker_error,
                OSError,
                ValueError,
            ) as exc:
                print(
                    "WARNING: Could not prepare runtime log capture for "
                    f"{subject_id}: {exc}",
                    file=sys.stderr,
                )

    active_subject_id: list[str | None] = [None]
    runtime_log_since: dict[str, datetime] = {}
    runtime_log_session_dir: list[Path | None] = [None]

    def begin_runtime_log_window(subject_id: str) -> None:
        if not args.capture_runtime_logs or subject_id not in runtime_leases:
            return
        runtime_log_since.setdefault(subject_id, datetime.now(timezone.utc))

    def capture_runtime_log(subject_id: str | None) -> None:
        if subject_id is None or not args.capture_runtime_logs:
            return
        since = runtime_log_since.pop(subject_id, None)
        session_dir = runtime_log_session_dir[0]
        lease = runtime_leases.get(subject_id)
        if since is None or session_dir is None or lease is None:
            return

        try:
            log_path = capture_subject_runtime_log(
                lease=lease,
                subject_id=subject_id,
                since=since,
                session_dir=session_dir,
            )
        except (runtime_log_capture_error, OSError, ValueError) as exc:
            print(
                f"WARNING: Runtime log capture failed for {subject_id}: {exc}",
                file=sys.stderr,
            )
            return

        print("Runtime diagnostics:")
        print(f"  {subject_id}:")
        _print_labeled_fields(
            [("container log", log_path.relative_to(session_dir).as_posix())],
            indent=4,
        )

    def prepare_subject_runtime(subject_id: str) -> None:
        if not args.start_if_needed:
            return
        begin_runtime_log_window(subject_id)
        if subject_id in ready_subject_ids and runtime_leases[subject_id].is_running:
            active_subject_id[0] = subject_id
            return

        _ensure_runtime_ready(
            subject_id=subject_id,
            lease=runtime_leases[subject_id],
            binding=subject_bindings[subject_id],
            repo_root=repo_root,
            runtime_root=runtime_root,
            startup_timeout_seconds=args.startup_timeout_seconds,
            startup_max_attempts=args.startup_max_attempts,
        )
        ready_subject_ids.add(subject_id)
        active_subject_id[0] = subject_id

    def before_planned_run(planned: planned_run) -> None:
        previous = active_subject_id[0]
        if previous == planned.subject_id:
            return

        if previous is not None:
            capture_runtime_log(previous)

        if (
            args.start_if_needed
            and previous is not None
            and runtime_leases[previous].started_by_acmm
        ):
            runtime_leases[previous].release()
            print("Runtime release:")
            print(f"  {previous}:")
            _print_labeled_fields([("result", "stopped")], indent=4)
            ready_subject_ids.discard(previous)

        if args.start_if_needed:
            prepare_subject_runtime(planned.subject_id)
            return

        begin_runtime_log_window(planned.subject_id)
        active_subject_id[0] = planned.subject_id

    if args.prestart_selected_subjects:
        print("Runtime pre-start:")
        try:
            for subject_id in selected_subject_ids:
                begin_runtime_log_window(subject_id)
                _ensure_runtime_ready(
                    subject_id=subject_id,
                    lease=runtime_leases[subject_id],
                    binding=subject_bindings[subject_id],
                    repo_root=repo_root,
                    runtime_root=runtime_root,
                    startup_timeout_seconds=args.startup_timeout_seconds,
                    startup_max_attempts=args.startup_max_attempts,
                    heading=None,
                )
                ready_subject_ids.add(subject_id)
        except (
            infrastructure_error,
            profile_error,
            docker_error,
            OSError,
            ValueError,
        ) as exc:
            cleanup_errors = cleanup_runtime_resources()
            for error in cleanup_errors:
                print(
                    f"WARNING: Runtime cleanup failed: {error}",
                    file=sys.stderr,
                )
            print(
                f"ERROR: Could not pre-start selected subject runtimes: {exc}",
                file=sys.stderr,
            )
            print("ERROR: No execution session was created.", file=sys.stderr)
            return 2

    _print_experiment_execution_selection(
        plan=plan,
        selected_runs=selected_runs,
        selected_indices=selected_indices,
    )
    _print_subject_runtime_details(subject_runtime_details)

    if args.start_if_needed:
        try:
            prepare_subject_runtime(selected_runs[0].subject_id)
        except (infrastructure_error, profile_error, docker_error, OSError, ValueError) as exc:
            cleanup_errors = cleanup_runtime_resources()
            for error in cleanup_errors:
                print(f"WARNING: Runtime cleanup failed: {error}", file=sys.stderr)
            print(
                f"ERROR: Could not acquire the first selected subject runtime: {exc}",
                file=sys.stderr,
            )
            print("ERROR: No execution session was created.", file=sys.stderr)
            return 2

    subject_adapters = {
        subject_id: subject_bindings[subject_id].adapter_builder()
        for subject_id in selected_subject_ids
    }
    selected_baselines = {
        subject_id: load_llm_baseline(
            repo_root,
            subject_bindings[subject_id].llm_baseline_id,
        )
        for subject_id in selected_subject_ids
    }
    overlays = {
        subject_id: runtime_provenance_configuration(
            selected_baselines[subject_id],
            subject_bindings[subject_id].infrastructure_subject,
        )
        for subject_id in selected_subject_ids
    }
    ollama_usage_models: list[str] = []
    for subject_id in selected_subject_ids:
        baseline = selected_baselines[subject_id]
        if baseline.logical_provider != "ollama_cloud":
            continue
        if baseline.model_name not in ollama_usage_models:
            ollama_usage_models.append(baseline.model_name)
        if subject_bindings[subject_id].infrastructure_subject == "agent-zero":
            utility_model = baseline.agent_zero.utility_model_name
            if utility_model not in ollama_usage_models:
                ollama_usage_models.append(utility_model)

    ollama_api_key: str | None = None
    ollama_credential_error: str | None = None
    if ollama_usage_models:
        try:
            ollama_api_key = resolve_ollama_cloud_api_key()
        except secret_binding_error as exc:
            ollama_credential_error = (
                f"ACMM core secret binding '{OLLAMA_CLOUD_API_KEY_SECRET_ID}' "
                "is unavailable"
            )
            print(
                "Provider usage telemetry: Ollama Cloud credential unavailable "
                f"from Core secret bindings ({exc}).",
                file=sys.stderr,
            )
        else:
            print(
                "Provider usage telemetry: Ollama Cloud credential resolved from "
                f"Core secret binding '{OLLAMA_CLOUD_API_KEY_SECRET_ID}'."
            )

    expected_ollama_request_count = sum(
        1
        for planned in selected_runs
        if selected_baselines[planned.subject_id].logical_provider == "ollama_cloud"
    )
    provider_usage_monitor = (
        ollama_cloud_usage_monitor(
            tuple(ollama_usage_models),
            api_key=ollama_api_key,
            credential_error=ollama_credential_error,
            expected_minimum_request_count=expected_ollama_request_count,
        )
        if ollama_usage_models
        else None
    )
    subject_admission_contexts = {
        subject_id: (
            subject_bindings[subject_id].infrastructure_subject,
            subject_bindings[subject_id].llm_baseline_id,
        )
        for subject_id in selected_subject_ids
    }

    def resolve_subject_admission_manager(subject_id: str):
        lease = runtime_leases.get(subject_id)
        if lease is not None:
            return lease.context.manager
        binding = subject_bindings[subject_id]
        return discover_runtime_lease(
            repo_root,
            runtime_root,
            binding.infrastructure_subject,
            binding.llm_baseline_id,
        ).context.manager

    admission_controller = compose_staged_subject_admission_controller(
        package,
        subject_admission_contexts,
        resolve_subject_admission_manager,
    )
    registry = compose_execution_registry(
        loaded_runtime.runtime,
        subject_adapters,
        tool_result_error_interpreters=staged_tool_result_error_interpreters(package),
        subject_configuration_overlays=overlays,
        admission_controller=admission_controller,
        materialization_group_by=tuple(materialization_group_by),
    )

    pacing = contract["run_protocol"].get("pacing", {})
    inter_run_delay = pacing.get("inter_run_delay_seconds", 0)

    if len(selected_runs) > 1:
        print(
            "Inter-run pacing: "
            f"{inter_run_delay:g}s between selected planned runs."
        )

    campaign_journal = [None]

    def session_ready(session_dir: Path) -> None:
        runtime_log_session_dir[0] = session_dir
        if campaign_context is not None:
            campaign_journal[0] = initialize_campaign_execution(campaign_context)

    def campaign_event(event_type: str, payload: Mapping[str, object]) -> None:
        if campaign_journal[0] is not None:
            campaign_journal[0].emit(event_type, payload)

    cleanup_errors: tuple[str, ...] = ()
    try:
        result = execute_experiment(
            contract=contract,
            contract_path=contract_path,
            registry=registry,
            results_root=Path(args.results_root),
            harness_version=_harness_version(),
            session_parent=(
                campaign_context.session_parent
                if campaign_context is not None
                else None
            ),
            event_sink=(campaign_event if campaign_context is not None else None),
            execution_id_factory=(
                (lambda: campaign_context.execution_id)
                if campaign_context is not None
                else None
            ),
            max_attempts_per_run=args.max_attempts_per_run,
            run_indices=selected_indices,
            progress=lambda message: print(message, flush=True),
            before_run=(
                before_planned_run
                if args.start_if_needed or args.capture_runtime_logs
                else None
            ),
            session_ready=(
                session_ready
                if args.capture_runtime_logs or campaign_context is not None
                else None
            ),
            package_provenance=_execution_package_provenance(package),
            provider_usage_monitor=provider_usage_monitor,
            capture_environment_snapshots=bool(
                getattr(args, "capture_environment_snapshots", False)
            ),
            deployment_reconciliation=(
                {
                    "permitted": bool(args.apply_if_needed),
                    "initial_state": (
                        "ready" if initial_deployment_ready else "divergent"
                    ),
                    "performed": reconciliation_performed,
                    "subjects": (
                        list(selected_subject_ids)
                        if reconciliation_performed
                        else []
                    ),
                    "final_state": "ready",
                }
                if args.apply_if_needed or reconciliation_performed
                else None
            ),
        )
    except experiment_execution_interrupted as exc:
        capture_runtime_log(active_subject_id[0])
        cleanup_errors = cleanup_runtime_resources()
        result = exc.result
        if campaign_context is not None:
            finalize_campaign_execution(
                campaign_context,
                result,
                status="interrupted",
            )
        _validate_persisted_execution(result, contract)

        try:
            summary_path = write_interruption_summary(result)
        except OSError as summary_exc:
            summary_path = None
            print(
                f"WARNING: Could not persist interruption summary: {summary_exc}",
                file=sys.stderr,
            )

        _print_execution_interruption(result, summary_path)
        for error in cleanup_errors:
            print(f"WARNING: Runtime cleanup failed: {error}", file=sys.stderr)
        return 130
    except (
        infrastructure_error,
        profile_error,
        docker_error,
        OSError,
        ValueError,
    ) as exc:
        capture_runtime_log(active_subject_id[0])
        cleanup_errors = cleanup_runtime_resources()
        if campaign_context is not None:
            finalize_campaign_execution_failure(
                campaign_context,
                detail=str(exc) or exc.__class__.__name__,
            )
        print(f"ERROR: Experiment execution failed: {exc}", file=sys.stderr)
        for error in cleanup_errors:
            print(f"WARNING: Runtime cleanup failed: {error}", file=sys.stderr)
        return 2

    capture_runtime_log(active_subject_id[0])
    cleanup_errors = cleanup_runtime_resources()
    if campaign_context is not None:
        finalize_campaign_execution(
            campaign_context,
            result,
            status="completed",
        )

    validation_status = _validate_persisted_execution(
        result,
        contract,
    )

    if validation_status != 0:
        return validation_status

    try:
        summary_path = write_execution_summary(result)
    except OSError as exc:
        summary_path = None
        print(
            f"WARNING: Could not persist execution summary: {exc}",
            file=sys.stderr,
        )

    _print_execution_completion(result, summary_path)

    if cleanup_errors:
        for error in cleanup_errors:
            print(f"ERROR: Runtime cleanup failed: {error}", file=sys.stderr)
        return 2

    if result.unresolved_run_indices:
        print(
            "Execution completed, but scientifically valid execution was not "
            "obtained for run index/indices: "
            + ", ".join(str(index) for index in result.unresolved_run_indices)
            + "."
        )
        return 1

    return 0



def _run_integration_resource_progress(
    integration_id: str,
    state: str,
) -> None:
    messages = {
        "runtime-running": "already running",
        "runtime-stopped": "stopped",
        "starting": "starting for experiment execution",
        "started": "ready for experiment execution",
    }
    detail = messages.get(state, state)
    print(f"Integration resource: {integration_id}: {detail}", flush=True)


def _validate_persisted_execution(
    result: experiment_execution_result | experiment_interruption_result,
    contract: dict[str, Any],
) -> int:
    try:
        record_schema = load_run_record_schema(
            default_run_record_schema_path()
        )
    except (OSError, ValueError) as exc:
        print(
            f"ERROR: Could not load Run Record schema: {exc}",
            file=sys.stderr,
        )
        return 2

    for slot in result.slots:
        for attempt in slot.attempts:
            record_path = attempt.result_path / "run_record.yaml"

            try:
                record = load_run_record(record_path)
            except run_record_load_error as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2

            validation = validate_run_record(
                record,
                record_schema,
                contract,
                expected_contract_sha256=result.contract_sha256,
            )

            if not validation.is_valid:
                _print_validation_result(validation)
                print(
                    f"ERROR: Persisted Run Record failed validation: "
                    f"{record_path}",
                    file=sys.stderr,
                )
                return 2

    return 0


def _print_execution_interruption(
    result: experiment_interruption_result,
    summary_path: Path | None,
) -> None:
    print("")
    print("Experiment execution interrupted by Ctrl+C.")
    print(f"  session:       {result.session_dir}")
    print(f"  interruption:  {result.interruption_path}")

    if summary_path is not None:
        print(f"  summary:       {summary_path}")
    else:
        print("  summary:       unavailable")

    print(
        f"  selected:      {len(result.selected_run_indices)} "
        f"of {result.contract_run_count}"
    )
    print(
        f"  resolved:      {len(result.resolved_run_indices)} "
        f"of {len(result.selected_run_indices)} selected"
    )
    print(
        "  unfinished:    "
        + _compact_integer_ranges(result.unresolved_run_indices)
    )

    if result.interrupted_run_index is not None:
        detail = f"run {result.interrupted_run_index}"

        if result.interrupted_attempt is not None:
            detail += f", attempt {result.interrupted_attempt}"

        print(f"  interrupted at: {detail}")

    print(f"  started:       {format_execution_timestamp(result.started_at)}")
    print(f"  interrupted:   {format_execution_timestamp(result.ended_at)}")
    print(f"  duration:      {format_execution_duration(result)}")
    print("  completion manifest: not written")
    print("  continuation: unsupported; start a new execution instead")

    request_count, detail = execution_provider_usage_summary(result.interruption_path)
    print("  provider usage:")
    if request_count is None:
        print(f"    Ollama Cloud requests: N/A ({detail})")
    else:
        print(f"    Ollama Cloud requests: {request_count} ({detail})")


def _print_execution_completion(
    result: experiment_execution_result,
    summary_path: Path | None,
) -> None:
    selected_attempts = [
        attempt
        for slot in result.slots
        for attempt in slot.attempts
        if attempt.run_id == slot.selected_run_id
    ]
    termination_counts = Counter(
        attempt.termination
        for attempt in selected_attempts
    )
    termination_summary = ", ".join(
        f"{termination}={count}"
        for termination, count in sorted(termination_counts.items())
    )

    print("")
    print("Experiment execution persisted successfully.")
    print(f"  session:      {result.session_dir}")
    print(f"  manifest:     {result.manifest_path}")

    if summary_path is not None:
        print(f"  summary:      {summary_path}")
    else:
        print("  summary:      unavailable")

    print(
        f"  selected:     {len(result.slots)} of {result.contract_run_count}"
    )
    print(
        f"  resolved:     "
        f"{len(result.slots) - len(result.unresolved_run_indices)} "
        f"of {len(result.slots)} selected"
    )
    print(f"  attempts:     {sum(len(slot.attempts) for slot in result.slots)}")

    if termination_summary:
        print(f"  terminations: {termination_summary}")

    print(f"  started:      {format_execution_timestamp(result.started_at)}")
    print(f"  ended:        {format_execution_timestamp(result.ended_at)}")
    print(f"  duration:     {format_execution_duration(result)}")

    request_count, detail = execution_provider_usage_summary(result.manifest_path)
    print("  provider usage:")
    if request_count is None:
        print(f"    Ollama Cloud requests: N/A ({detail})")
    else:
        print(f"    Ollama Cloud requests: {request_count} ({detail})")


def _run_export(args: argparse.Namespace) -> int:
    try:
        result = export_execution(
            execution_dir=Path(args.execution),
            output_root=Path(args.output_root),
            contract_schema_path=(
                Path(args.contract_schema)
                if args.contract_schema is not None
                else None
            ),
            run_record_schema_path=(
                Path(args.record_schema)
                if args.record_schema is not None
                else None
            ),
        )
    except (analysis_export_error, FileExistsError, OSError, ValueError) as exc:
        print(f"ERROR: Analysis export failed: {exc}", file=sys.stderr)
        return 2

    print("Analysis export completed successfully.")
    print(f"  source execution: {result.execution_id}")
    print(f"  output:           {result.output_dir}")
    print(f"  runs:             {result.runs_path} ({result.run_rows} row(s))")
    print(
        f"  judgments:        {result.judgments_path} "
        f"({result.judgment_rows} row(s))"
    )
    print(f"  manifest:         {result.manifest_path}")
    return 0


def _run_validate_record(args: argparse.Namespace) -> int:
    record_path = Path(args.record)
    contract_path = Path(args.contract)

    contract_schema_path = (
        Path(args.contract_schema)
        if args.contract_schema is not None
        else None
    )
    record_schema_path = (
        Path(args.record_schema)
        if args.record_schema is not None
        else default_run_record_schema_path()
    )

    contract, _, status = _load_and_validate_contract(
        contract_path,
        contract_schema_path,
    )

    if status != 0:
        print("Run Record validation was not attempted because the Contract is invalid.")
        return status

    assert contract is not None

    try:
        record = load_run_record(record_path)
    except run_record_load_error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        record_schema = load_run_record_schema(record_schema_path)
    except (OSError, ValueError) as exc:
        print(
            f"ERROR: Could not load Run Record schema "
            f"'{record_schema_path}': {exc}",
            file=sys.stderr,
        )
        return 2

    try:
        contract_sha256 = sha256_file(contract_path)
    except OSError as exc:
        print(
            f"ERROR: Could not hash Contract '{contract_path}': {exc}",
            file=sys.stderr,
        )
        return 2

    result = validate_run_record(
        record,
        record_schema,
        contract,
        expected_contract_sha256=contract_sha256,
    )
    _print_validation_result(result)

    if not result.is_valid:
        print(
            f"Run Record validation failed with {len(result.errors)} error(s) "
            f"and {len(result.warnings)} warning(s)."
        )
        return 1

    validity = record["execution"]["validity"]

    print(
        f"Run Record is valid with {len(result.warnings)} warning(s): "
        f"{record_path}"
    )
    print(f"Execution validity: {validity}")
    return 0



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acmm",
        description="ACMM experiment tooling.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    register_environment_commands(subparsers)
    register_campaign_commands(subparsers)
    register_experiment_package_commands(subparsers)
    register_secret_commands(subparsers)

    run_parser = subparsers.add_parser(
        "run",
        help="Run an installed Experiment Package or enter engineering apparatus commands.",
        formatter_class=_run_help_formatter,
    )
    run_parser.add_argument(
        "experiment",
        help="Installed Experiment Package selector for scientific execution.",
    )
    run_index_group = run_parser.add_mutually_exclusive_group()
    run_index_group.add_argument(
        "--run-index",
        action="append",
        metavar="INDEX|START-END",
        help=(
            "Select a global planned-run index or inclusive range (for example "
            "20-39). Repeat to combine selectors. Overlaps are deduplicated."
        ),
    )
    run_index_group.add_argument(
        "--subject-run-index",
        action="append",
        metavar="INDEX|START-END",
        help=(
            "Select a zero-based run index or inclusive range relative to each "
            "selected --subject. Requires --subject. Repeat to combine selectors; "
            "global run identities remain unchanged."
        ),
    )
    run_parser.add_argument(
        "--subject",
        action="append",
        metavar="SUBJECT_ID",
        help=(
            "Select a Contract subject (agent) ID. Repeat to select multiple "
            "subjects. With --run-index, filters are intersected; with "
            "--subject-run-index, relative indices are applied independently "
            "within each selected subject."
        ),
    )
    run_parser.add_argument(
        "--scenario",
        action="append",
        metavar="SCENARIO_ID",
        help=(
            "Select a Contract scenario ID. Repeat to select multiple scenarios. "
            "This semantic filter is independent of counterbalanced plan position "
            "and may be combined with subject and repetition filters."
        ),
    )
    run_parser.add_argument(
        "--repetition",
        action="append",
        metavar="N|START-END",
        help=(
            "Select a 1-based Contract repetition number or inclusive range. "
            "Repeat to combine selectors. This semantic filter is independent of "
            "counterbalanced plan position."
        ),
    )
    run_parser.add_argument(
        "--preview",
        action="store_true",
        help=(
            "Validate and print the resolved run selection without creating an "
            "execution session or contacting subject runtimes."
        ),
    )
    run_parser.add_argument(
        "--apply-if-needed",
        action="store_true",
        help=(
            "For real execution, permit ACMM to reconcile shared integration "
            "resources and selected subject experiment context when deployment "
            "preflight is not ready. A second strict deployment preflight must "
            "pass before execution."
        ),
    )
    run_parser.add_argument(
        "--start-if-needed",
        action="store_true",
        help=(
            "For real execution, permit ACMM to start selected subjects that "
            "are correctly deployed but currently stopped. ACMM stops only "
            "runtimes it started itself."
        ),
    )
    run_parser.add_argument(
        "--prestart-selected-subjects",
        action="store_true",
        help=(
            "With --start-if-needed, acquire and live-readiness-check every "
            "selected subject before the execution session is created. ACMM "
            "still releases owned runtimes as subject blocks complete."
        ),
    )
    run_parser.add_argument(
        "--capture-runtime-logs",
        action="store_true",
        help=(
            "Capture sanitized timestamped per-subject container logs into the "
            "execution result directory. Diagnostic capture is best-effort and "
            "is not used for compliance adjudication."
        ),
    )
    run_parser.add_argument(
        "--capture-environment-snapshots",
        action="store_true",
        help=(
            "Ask the Experiment Package lifecycle to capture diagnostic pre/post "
            "environment snapshots for each selected run. Capture is package-defined, "
            "best-effort only when the package says so, and may perturb provider traffic; "
            "leave it disabled for final scientific acquisition."
        ),
    )
    run_parser.add_argument(
        "--startup-max-attempts",
        type=int,
        default=3,
        help=(
            "Maximum runtime acquisition attempts for each selected subject "
            "(default: 3). Failed attempts use progressive cooldowns of 1s, "
            "5s, 15s, 30s, then 60s for later retries."
        ),
    )
    run_parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum startup wait for a subject started by ACMM.",
    )
    run_parser.add_argument(
        "--results-root",
        default=str(default_results_root()),
        help=(
            "Result root directory "
            f"(default: {default_results_root()})."
        ),
    )
    run_parser.add_argument(
        "--max-attempts-per-run",
        type=int,
        default=1,
        help=(
            "Maximum attempts for an invalid execution of a selected run "
            "(default: 1)."
        ),
    )
    run_parser.set_defaults(handler=_run_command)


    export_parser = subparsers.add_parser(
        "export",
        help=(
            "Export one completed experiment execution into deterministic "
            "analysis-ready CSV tables."
        ),
    )
    export_parser.add_argument(
        "execution",
        help=(
            "Path to a completed execution_<id> directory containing "
            "contract.yaml and experiment_execution.yaml."
        ),
    )
    export_parser.add_argument(
        "--output-root",
        default=str(default_exports_root()),
        help=(
            "Analysis export root directory "
            f"(default: {default_exports_root()})."
        ),
    )
    export_parser.add_argument(
        "--record-schema",
        help=(
            "Path to a Run Record JSON Schema. "
            "Defaults to records/v0_1/run_record.schema.json."
        ),
    )
    export_parser.add_argument(
        "--contract-schema",
        help=(
            "Path to a Contract JSON Schema. "
            "Defaults to the bundled schema selected by the Contract schema_version."
        ),
    )
    export_parser.set_defaults(handler=_run_export)

    record_parser = subparsers.add_parser(
        "validate-record",
        help="Validate an ACMM Run Record against its Experiment Contract.",
    )
    record_parser.add_argument(
        "record",
        help="Path to a Run Record YAML file.",
    )
    record_parser.add_argument(
        "--contract",
        required=True,
        help="Path to the Experiment Contract used for the run.",
    )
    record_parser.add_argument(
        "--record-schema",
        help=(
            "Path to a Run Record JSON Schema. "
            "Defaults to records/v0_1/run_record.schema.json in this repository."
        ),
    )
    record_parser.add_argument(
        "--contract-schema",
        help=(
            "Path to a Contract JSON Schema. "
            "Defaults to the bundled schema selected by the Contract schema_version."
        ),
    )
    record_parser.set_defaults(handler=_run_validate_record)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parse_cli_arguments(argv)

    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted before an execution could be finalized.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
