from __future__ import annotations

import argparse
from dataclasses import asdict
from functools import wraps
import json
import sys

from acmm.experiment_package import (
    acquire_staged_integration_resources,
    doctor_staged_integration_resources,
    doctor_staged_experiment_runtime,
    down_staged_integration_resources,
    open_staged_experiment_package,
    restore_staged_integration_resources,
    status_staged_integration_resources,
)
from acmm_infra.cohort import (
    apply_cohort,
    doctor_cohort,
    inspect_cohort,
    resolve_baseline_cohort,
    resolve_deployed_cohort,
    stop_cohort,
    summarize_context,
)
from acmm_infra.common import infrastructure_error
from acmm_infra.docker import docker_error
from acmm_infra.profiles.common import (
    default_repo_root,
    default_runtime_root,
    profile_error,
)


def _safe_environment_handler(handler):
    @wraps(handler)
    def run(args: argparse.Namespace) -> int:
        try:
            return handler(args)
        except (profile_error, infrastructure_error, docker_error, OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    return run


def _environment_readiness_summary(
    *,
    context_ready,
    resources_ready,
    runtime_ready,
    subject_ready,
    resource_reports,
    resource_restore_errors,
    runtime_reports,
    runtime_error,
    doctor_results,
):
    blockers = []

    if not context_ready:
        blockers.append(
            {
                "source": "deployment_context",
                "detail": "deployment context is not fully converged",
            }
        )

    for integration_id, report in resource_reports:
        for check in report.checks:
            if not check.ok:
                blockers.append(
                    {
                        "source": f"integration:{integration_id}:{check.name}",
                        "detail": check.detail,
                    }
                )
    for integration_id, detail in resource_restore_errors:
        blockers.append(
            {
                "source": f"integration:{integration_id}:cleanup",
                "detail": detail,
            }
        )

    if runtime_error is not None:
        blockers.append(
            {
                "source": "runtime:load",
                "detail": runtime_error,
            }
        )
    for diagnostic_id, report in runtime_reports:
        for check in report.checks:
            if not check.ok:
                blockers.append(
                    {
                        "source": f"runtime:{diagnostic_id}:{check.name}",
                        "detail": check.detail,
                    }
                )

    for item in doctor_results:
        if item.is_ready:
            continue
        result = item.doctor
        found = False
        if result.error is not None:
            blockers.append(
                {
                    "source": f"subject:{item.subject.subject_id}:doctor",
                    "detail": result.error,
                }
            )
            found = True
        elif result.report is not None:
            for check in result.report.checks:
                if not check.ok:
                    blockers.append(
                        {
                            "source": (
                                f"subject:{item.subject.subject_id}:{check.name}"
                            ),
                            "detail": check.detail,
                        }
                    )
                    found = True
        if item.release_error is not None:
            blockers.append(
                {
                    "source": f"subject:{item.subject.subject_id}:cleanup",
                    "detail": item.release_error,
                }
            )
            found = True
        if not found:
            blockers.append(
                {
                    "source": f"subject:{item.subject.subject_id}",
                    "detail": "subject doctor did not reach a ready state",
                }
            )

    resource_ready_count = sum(report.is_ready for _, report in resource_reports)
    runtime_ready_count = sum(report.is_ready for _, report in runtime_reports)
    runtime_total = len(runtime_reports)
    if runtime_error is not None:
        runtime_total += 1
    subject_ready_count = sum(item.is_ready for item in doctor_results)

    return {
        "components": {
            "deployment_context": {"ready": context_ready},
            "integration_resources": {
                "ready": resources_ready,
                "ready_count": resource_ready_count,
                "total": len(resource_reports),
            },
            "runtime_diagnostics": {
                "ready": runtime_ready,
                "ready_count": runtime_ready_count,
                "total": runtime_total,
            },
            "subjects": {
                "ready": subject_ready,
                "ready_count": subject_ready_count,
                "total": len(doctor_results),
            },
        },
        "blockers": blockers,
    }


def _print_environment_readiness_summary(summary) -> None:
    components = summary["components"]
    print("readiness summary:")
    print(
        "  deployment context: "
        + ("READY" if components["deployment_context"]["ready"] else "NOT READY")
    )
    for key, label in (
        ("integration_resources", "integration resources"),
        ("runtime_diagnostics", "runtime diagnostics"),
        ("subjects", "subjects"),
    ):
        item = components[key]
        state = "READY" if item["ready"] else "NOT READY"
        if item["total"]:
            detail = f" ({item['ready_count']}/{item['total']})"
        else:
            detail = " (none configured)"
        print(f"  {label}: {state}{detail}")

    blockers = summary["blockers"]
    if blockers:
        print("  blockers:")
        for blocker in blockers:
            print(f"    - {blocker['source']}: {blocker['detail']}")


def register_environment_commands(subparsers: argparse._SubParsersAction) -> None:
    env_parser = subparsers.add_parser(
        "env",
        help="Prepare and inspect qualified ACMM subject environments.",
    )
    env_subparsers = env_parser.add_subparsers(
        dest="env_command",
        required=True,
    )
    _register_environment_subcommands(env_subparsers)


def _register_environment_subcommands(
    subparsers: argparse._SubParsersAction,
) -> None:
    set_llm_parser = subparsers.add_parser(
        "set-llm",
        help=(
            "Reconcile qualified subjects to a registered LLM and remove any "
            "active Experiment Package state."
        ),
    )
    set_llm_parser.add_argument(
        "llm_id",
        help="Registered LLM ID to deploy across the selected qualified subjects.",
    )
    _add_subject_arguments(set_llm_parser)
    set_llm_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum provisioning timeout passed to each subject manager.",
    )
    set_llm_parser.set_defaults(
        handler=_safe_environment_handler(run_environment_set_llm)
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Show the discovered state of qualified ACMM subject deployments.",
    )
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=_safe_environment_handler(run_environment_status))

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check live readiness of the current qualified ACMM subject environment.",
    )
    doctor_parser.add_argument(
        "--start-if-needed",
        action="store_true",
        help=(
            "Temporarily start stopped subjects one at a time for doctor checks, "
            "then restore their original stopped state."
        ),
    )
    doctor_parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum startup wait for a temporarily started subject.",
    )
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(handler=_safe_environment_handler(run_environment_doctor))

    stop_parser = subparsers.add_parser(
        "stop",
        help="Stop running qualified ACMM subject runtimes without changing package state.",
    )
    _add_subject_arguments(stop_parser)
    stop_parser.add_argument("--json", action="store_true")
    stop_parser.set_defaults(handler=_safe_environment_handler(run_environment_stop))


def run_environment_set_llm(args: argparse.Namespace) -> int:
    repo_root = default_repo_root().expanduser().resolve()
    runtime_root = default_runtime_root().expanduser().resolve()
    selected = tuple(args.subjects or ())
    cohort = resolve_baseline_cohort(
        repo_root,
        selected,
        llm_baseline_id=args.llm_id,
    )

    print("ACMM environment set-llm:")
    print(f"  LLM:                {args.llm_id}")
    print(f"  subjects:           {len(cohort.subjects)}")
    for subject in cohort.subjects:
        print(
            f"    {subject.subject_id} ({subject.infrastructure_subject}, "
            f"llm={subject.llm_baseline_id})"
        )

    def progress(index, total, subject, payload) -> None:
        if index is not None:
            print()
            print(f"[{index}/{total}] {subject.subject_id}")
            print(f"  infrastructure: {subject.infrastructure_subject}")
            print("  provisioning:")
            return
        print(f"    {payload}", flush=True)

    results = apply_cohort(
        repo_root,
        runtime_root,
        cohort,
        timeout_seconds=args.timeout_seconds,
        progress=progress,
    )

    print()
    print("Environment set-llm complete:")
    print(f"  subjects updated: {len(results)}/{len(cohort.subjects)}")
    print(f"  LLM:              {args.llm_id}")
    return 0


def run_environment_status(args: argparse.Namespace) -> int:
    repo_root = default_repo_root().expanduser().resolve()
    runtime_root = default_runtime_root().expanduser().resolve()
    cohort = resolve_deployed_cohort(
        repo_root,
        runtime_root,
    )
    statuses = inspect_cohort(repo_root, runtime_root, cohort.subjects)
    context = summarize_context(statuses)
    resource_statuses = ()
    if context.state == "converged" and context.experiment_id and context.package_sha256:
        package = open_staged_experiment_package(
            runtime_root, context.experiment_id, context.package_sha256
        )
        resource_statuses = status_staged_integration_resources(package, runtime_root)

    if args.json:
        payload = {
            "context": asdict(context),
            "subjects": [asdict(item) for item in statuses],
            "integration_resources": [
                {"integration_id": item.integration_id, "status": dict(item.value)}
                for item in resource_statuses
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print("ACMM environment status:")
    print("  deployment context:")
    print(f"    state:      {context.state}")
    print(f"    experiment: {context.experiment_id or 'none'}")
    if context.package_sha256 is not None:
        print(f"    package:    {context.package_sha256}")
    if resource_statuses:
        print("  integration resources:")
        for item in resource_statuses:
            print(f"    {item.integration_id}: {dict(item.value)}")
    print("  subjects:")
    for item in statuses:
        print(f"    {item.subject_id}:")
        print(f"      infrastructure:    {item.infrastructure_subject}")
        if item.llm is not None:
            _print_llm_observation(item.llm, indent=6, running=item.running)
        if item.error is not None:
            print(f"      state:             unavailable")
            print(f"      error:             {item.error}")
            continue
        print(f"      running:           {item.running}")
        print(f"      experiment:        {item.experiment_id or 'none'}")
        if item.package_sha256 is not None:
            print(f"      package SHA-256:   {item.package_sha256}")
        print(f"      package state:     {item.package_state}")
    _print_context_comparability(statuses)
    return 0


def run_environment_doctor(args: argparse.Namespace) -> int:
    repo_root = default_repo_root().expanduser().resolve()
    runtime_root = default_runtime_root().expanduser().resolve()
    cohort = resolve_deployed_cohort(
        repo_root,
        runtime_root,
    )
    contexts = inspect_cohort(
        repo_root,
        runtime_root,
        cohort.subjects,
        include_running=False,
    )
    context = summarize_context(contexts)
    package = None
    resource_leases = ()
    resource_reports = ()
    resource_restore_errors = ()
    runtime_reports = ()
    runtime_error = None

    def resource_progress(integration_id, event) -> None:
        if args.json:
            return
        if event == "runtime-running":
            print(f"[integration] {integration_id}")
            print("  runtime:        already running")
        elif event == "runtime-stopped":
            print(f"[integration] {integration_id}")
            print("  runtime:        stopped")
        elif event == "starting":
            print("  startup:        starting temporarily...", flush=True)
        elif event == "started":
            print("  startup:        ready")
        elif event == "restoring":
            print("  cleanup:        restoring stopped state...", flush=True)
        elif event == "restored":
            print("  cleanup:        restored")
            print()
        elif event == "restore-failed":
            print("  cleanup:        failed")
            print()
        else:
            print(f"  {event}")

    if not args.json:
        print("ACMM environment doctor progress:")

    if context.state == "converged" and context.experiment_id and context.package_sha256:
        package = open_staged_experiment_package(
            runtime_root, context.experiment_id, context.package_sha256
        )
        resource_leases = acquire_staged_integration_resources(
            package,
            runtime_root,
            start_if_needed=args.start_if_needed,
            timeout_seconds=args.startup_timeout_seconds,
            progress=resource_progress,
        )

    def progress(index, total, subject, event) -> None:
        if event == "begin":
            print(f"[{index}/{total}] {subject.subject_id}")
            print(f"  infrastructure: {subject.infrastructure_subject}")
        elif event == "runtime-running":
            print("  runtime:        already running")
        elif event == "runtime-stopped":
            print("  runtime:        stopped")
        elif event == "starting":
            print("  startup:        starting temporarily...", flush=True)
        elif event == "started":
            print("  startup:        ready")
        elif event == "checking":
            print("  doctor:         running checks...", flush=True)
        elif event == "restoring":
            print("  cleanup:        restoring stopped state...", flush=True)
        elif event == "restored":
            print("  cleanup:        restored")
        elif event == "restore-failed":
            print("  cleanup:        failed")
        elif event == "complete-ready":
            print("  result:         READY")
            print()
        elif event == "complete-not-ready":
            print("  result:         NOT READY")
            print()

    try:
        if package is not None:
            resource_reports = doctor_staged_integration_resources(
                package,
                runtime_root,
            )
            try:
                runtime_reports = doctor_staged_experiment_runtime(package)
            except Exception as exc:
                runtime_error = str(exc) or exc.__class__.__name__
        doctor_results = doctor_cohort(
            repo_root,
            runtime_root,
            cohort.subjects,
            start_if_needed=args.start_if_needed,
            startup_timeout_seconds=args.startup_timeout_seconds,
            progress=None if args.json else progress,
        )
    finally:
        if package is not None and resource_leases:
            resource_restore_errors = restore_staged_integration_resources(
                package,
                runtime_root,
                resource_leases,
                progress=resource_progress,
            )

    subject_ready = all(item.is_ready for item in doctor_results)
    if context.state == "baseline":
        context_ready = all(
            item.error is None
            and item.experiment_id is None
            and item.package_state == "none"
            for item in contexts
        )
    else:
        context_ready = (
            context.state == "converged"
            and all(item.package_state == "match" for item in contexts)
        )
    resources_ready = (
        all(report.is_ready for _, report in resource_reports)
        and not resource_restore_errors
    )
    runtime_ready = (
        runtime_error is None
        and all(report.is_ready for _, report in runtime_reports)
    )
    ready = subject_ready and context_ready and resources_ready and runtime_ready
    readiness_summary = _environment_readiness_summary(
        context_ready=context_ready,
        resources_ready=resources_ready,
        runtime_ready=runtime_ready,
        subject_ready=subject_ready,
        resource_reports=resource_reports,
        resource_restore_errors=resource_restore_errors,
        runtime_reports=runtime_reports,
        runtime_error=runtime_error,
        doctor_results=doctor_results,
    )

    if args.json:
        payload = {
            "ready": ready,
            "readiness_summary": readiness_summary,
            "context": asdict(context),
            "subjects": [],
            "integration_resources": [
                {
                    "integration_id": integration_id,
                    "ready": report.is_ready,
                    "checks": [asdict(check) for check in report.checks],
                    "restore_error": next(
                        (detail for iid, detail in resource_restore_errors if iid == integration_id),
                        None,
                    ),
                }
                for integration_id, report in resource_reports
            ],
            "runtime_diagnostics": [
                {
                    "diagnostic_id": diagnostic_id,
                    "ready": report.is_ready,
                    "checks": [asdict(check) for check in report.checks],
                }
                for diagnostic_id, report in runtime_reports
            ],
            "runtime_error": runtime_error,
        }
        for item in doctor_results:
            result = item.doctor
            payload["subjects"].append(
                {
                    "subject_id": item.subject.subject_id,
                    "infrastructure_subject": item.subject.infrastructure_subject,
                    "llm_baseline_id": item.subject.llm_baseline_id,
                    "experiment_id": result.experiment_id,
                    "ready": item.is_ready,
                    "error": result.error,
                    "initially_running": item.initially_running,
                    "started_by_acmm": item.started_by_acmm,
                    "release_error": item.release_error,
                    "llm": asdict(item.llm) if item.llm is not None else None,
                    "checks": (
                        [asdict(check) for check in result.report.checks]
                        if result.report is not None
                        else []
                    ),
                }
            )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if ready else 1

    print("ACMM environment doctor:")
    print("  deployment context:")
    print(f"    state:      {context.state}")
    print(f"    experiment: {context.experiment_id or 'none'}")
    if context.package_sha256 is not None:
        print(f"    package:    {context.package_sha256}")
    if resource_reports:
        print("  integration resources:")
        for integration_id, report in resource_reports:
            print(f"    {integration_id}: {'READY' if report.is_ready else 'NOT READY'}")
            for check in report.checks:
                if not check.ok:
                    print(f"      {check.name}: {check.detail}")
            for iid, detail in resource_restore_errors:
                if iid == integration_id:
                    print(f"      cleanup error: {detail}")
    if runtime_reports or runtime_error is not None:
        print("  runtime diagnostics:")
        if runtime_error is not None:
            print("    runtime: NOT READY")
            print(f"      error: {runtime_error}")
        for diagnostic_id, report in runtime_reports:
            print(
                f"    {diagnostic_id}: "
                f"{'READY' if report.is_ready else 'NOT READY'}"
            )
            for check in report.checks:
                state = "READY" if check.ok else "NOT READY"
                print(f"      {check.name}: {state} - {check.detail}")
    print("  subjects:")
    for item in doctor_results:
        subject = item.subject
        result = item.doctor
        state = item.deployment_state
        print(f"    {subject.subject_id}:")
        print(f"      infrastructure:    {subject.infrastructure_subject}")
        if item.llm is not None:
            _print_llm_observation(item.llm, indent=6, running=True)
        else:
            print(f"      LLM:               {subject.llm_baseline_id}")
        print(f"      readiness:         {'READY' if item.is_ready else 'NOT READY'}")
        print(f"      experiment:        {result.experiment_id or 'none'}")
        if item.initially_running is not None:
            print(f"      initially running: {item.initially_running}")
        if item.started_by_acmm:
            print("      temporary startup: started and restored")
        if isinstance(state, dict):
            package_state = state.get("package")
            if isinstance(package_state, dict):
                package_sha256 = package_state.get("sha256")
                if isinstance(package_sha256, str):
                    print(f"      package SHA-256:   {package_sha256}")
        if result.error is not None:
            print(f"      error:             {result.error}")
        elif result.report is not None:
            integration_successes = tuple(
                check
                for check in result.report.checks
                if check.ok and check.name.startswith("integration:")
            )
            if integration_successes:
                print("      integration checks:")
                for check in integration_successes:
                    print(f"        {check.name}: READY - {check.detail}")
            failures = tuple(check for check in result.report.checks if not check.ok)
            if failures:
                print("      failed checks:")
                for check in failures:
                    print(f"        {check.name}: {check.detail}")
        if item.release_error is not None:
            print(f"      cleanup error:     {item.release_error}")

    _print_context_comparability(doctor_results)
    print()
    if not context_ready:
        if context.state == "baseline":
            print(
                "Baseline cohort context is not fully converged with "
                "experiment-independent state."
            )
        else:
            print(
                "Experiment cohort context is not fully converged with the "
                "selected package revision."
            )
    if not ready:
        print()
        _print_environment_readiness_summary(readiness_summary)
        print()
    print("READY" if ready else "NOT READY")
    return 0 if ready else 1


def run_environment_stop(args: argparse.Namespace) -> int:
    repo_root = default_repo_root().expanduser().resolve()
    runtime_root = default_runtime_root().expanduser().resolve()
    cohort = resolve_deployed_cohort(
        repo_root,
        runtime_root,
        tuple(args.subjects or ()),
    )
    results = stop_cohort(repo_root, runtime_root, cohort.subjects)
    failed = any(item.error is not None for item in results)
    resource_results = ()
    resource_context_error = None
    if not args.subjects:
        statuses = inspect_cohort(repo_root, runtime_root, cohort.subjects, include_running=False)
        context = summarize_context(statuses)
        if context.state == "converged" and context.experiment_id and context.package_sha256:
            try:
                package = open_staged_experiment_package(
                    runtime_root, context.experiment_id, context.package_sha256
                )
                resource_results = down_staged_integration_resources(package, runtime_root)
                if any(item.error is not None for item in resource_results):
                    failed = True
            except Exception as exc:
                resource_context_error = str(exc)
                failed = True

    if args.json:
        payload = {
            "stopped": not failed,
            "subjects": [asdict(item) for item in results],
            "integration_resources": [asdict(item) for item in resource_results],
            "integration_resource_context_error": resource_context_error,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if failed else 0

    print("ACMM environment stop:")
    print("  subjects:")
    for item in results:
        print(f"    {item.subject_id}:")
        print(f"      infrastructure: {item.infrastructure_subject}")
        print(f"      initial state:  {'running' if item.initial_running else 'stopped'}")
        if item.error is not None:
            print("      result:         failed")
            print(f"      error:          {item.error}")
        elif item.stopped:
            print("      result:         stopped")
        else:
            print("      result:         unchanged")
    if resource_results or resource_context_error is not None:
        print("  integration resources:")
        for item in resource_results:
            print(f"    {item.integration_id}:")
            if item.initially_running is None:
                print("      initial state:  unknown")
            else:
                print(
                    "      initial state:  "
                    + ("running" if item.initially_running else "stopped")
                )
            if item.error is not None:
                print("      result:         failed")
                print(f"      error:          {item.error}")
            elif item.running_after:
                print("      result:         still running")
            elif item.initially_running:
                print("      result:         stopped")
            else:
                print("      result:         unchanged")
            if "container_present" in item.value:
                print(
                    "      container:      "
                    + ("present" if item.value.get("container_present") else "absent")
                )
        if resource_context_error is not None:
            print("    context:")
            print("      result:         failed")
            print(f"      error:          {resource_context_error}")
    return 1 if failed else 0


def _print_llm_observation(
    observation,
    *,
    indent: int,
    running: bool | None = None,
) -> None:
    prefix = " " * indent
    print(f"{prefix}LLM:                {observation.deployed_baseline_id}")
    if observation.expected_baseline_id != observation.deployed_baseline_id:
        print(f"{prefix}expected LLM:       {observation.expected_baseline_id}")
    print(f"{prefix}model:              {observation.model_name}")
    print(f"{prefix}provider:           {observation.logical_provider}")
    print(
        f"{prefix}context minimum:    "
        f"{observation.minimum_context_window}"
    )
    requested = observation.requested_context_window
    print(f"{prefix}context requested:  {requested if requested is not None else 'not controlled'}")
    observed = observation.observed_context_window
    if observed is not None:
        print(f"{prefix}context observed:   {observed}")
    elif running is False:
        print(
            f"{prefix}context observed:   N/A "
            "(runtime stopped; use env doctor --start-if-needed)"
        )
    else:
        print(f"{prefix}context observed:   unavailable")
    if observation.context_observation_kind != "unknown":
        print(f"{prefix}context kind:       {observation.context_observation_kind}")
    if observation.context_observation_source is not None:
        print(f"{prefix}context source:     {observation.context_observation_source}")
    if observation.observed_model_name is not None:
        print(f"{prefix}model observed:     {observation.observed_model_name}")
    if observation.model_observation_source is not None:
        print(f"{prefix}model source:       {observation.model_observation_source}")
    if observation.provider_model_available is not None:
        state = "yes" if observation.provider_model_available else "no"
        print(f"{prefix}model available:    {state}")
    if observation.provider_model_availability_source is not None:
        print(
            f"{prefix}availability source:{observation.provider_model_availability_source}"
        )
    if observation.model_matches_baseline is False:
        print(f"{prefix}model state:        WARNING: observed model differs from configured LLM")
    if observation.context_meets_minimum is False:
        print(f"{prefix}context state:      WARNING: observed context is below minimum")
    elif observation.context_matches_requested is False:
        print(f"{prefix}context state:      WARNING: observed context differs from request")


def _print_context_comparability(statuses) -> None:
    configured = []
    capability = []
    for item in statuses:
        observation = item.llm
        if observation is None or observation.observed_context_window is None:
            continue
        subject_id = getattr(item, "subject_id", None)
        if subject_id is None and hasattr(item, "subject"):
            subject_id = item.subject.subject_id
        pair = (str(subject_id), observation.observed_context_window)
        if observation.context_observation_kind == "configured_effective":
            configured.append(pair)
        else:
            capability.append(pair)

    if not configured and not capability:
        return

    print("  context-window observability:")
    if configured:
        rendered = ", ".join(f"{name}={value}" for name, value in configured)
        print(f"    configured/effective: {rendered}")
        values = {value for _, value in configured}
        state = "equal" if len(values) == 1 else "different"
        print(f"    comparable state:     {state}")
    if capability:
        rendered = ", ".join(f"{name}={value}" for name, value in capability)
        print(f"    capability metadata:  {rendered}")



def _add_subject_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--subject",
        dest="subjects",
        action="append",
        help=(
            "Restrict the cohort to a qualified ACMM subject ID. Repeat to "
            "select multiple subjects."
        ),
    )
