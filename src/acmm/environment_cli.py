from __future__ import annotations

import argparse
from dataclasses import asdict
from functools import wraps
import json
import sys

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
        except (profile_error, infrastructure_error, docker_error, OSError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    return run


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
            "active experiment apparatus."
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
        help="Stop running qualified ACMM subject runtimes without changing apparatus.",
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

    if args.json:
        payload = {
            "context": asdict(context),
            "subjects": [asdict(item) for item in statuses],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print("ACMM environment status:")
    print("  deployment context:")
    print(f"    state:      {context.state}")
    print(f"    experiment: {context.experiment_id or 'none'}")
    if context.package_sha256 is not None:
        print(f"    package:    {context.package_sha256}")
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
        print(f"      application scope: {item.application_scope or 'none'}")
        print(f"      apparatus:         {item.apparatus_state}")
        if item.source_sha256 is not None:
            print(f"      apparatus SHA-256: {item.source_sha256}")
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

    if not args.json:
        print("ACMM environment doctor progress:")

    doctor_results = doctor_cohort(
        repo_root,
        runtime_root,
        cohort.subjects,
        start_if_needed=args.start_if_needed,
        startup_timeout_seconds=args.startup_timeout_seconds,
        progress=None if args.json else progress,
    )

    subject_ready = all(item.is_ready for item in doctor_results)
    if context.state == "baseline":
        context_ready = all(
            item.error is None
            and item.experiment_id is None
            and item.application_scope is None
            and item.apparatus_state == "none"
            for item in contexts
        )
    else:
        context_ready = (
            context.state == "converged"
            and all(item.application_scope == "experiment" for item in contexts)
            and all(item.apparatus_state == "match" for item in contexts)
        )
    ready = subject_ready and context_ready

    if args.json:
        payload = {
            "ready": ready,
            "context": asdict(context),
            "subjects": [],
        }
        for item in doctor_results:
            result = item.doctor
            state = item.deployment_state
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
                    "application_scope": (
                        state.get("application_scope")
                        if isinstance(state, dict)
                        else None
                    ),
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
    print("  subjects:")
    for item in doctor_results:
        subject = item.subject
        result = item.doctor
        state = item.deployment_state
        print(f"    {subject.subject_id}:")
        print(f"      infrastructure:    {subject.infrastructure_subject}")
        print(f"      LLM:               {subject.llm_baseline_id}")
        if item.llm is not None:
            _print_llm_observation(
                item.llm,
                indent=6,
                running=True,
            )
        print(f"      readiness:         {'READY' if item.is_ready else 'NOT READY'}")
        print(f"      experiment:        {result.experiment_id or 'none'}")
        if item.initially_running is not None:
            print(f"      initially running: {item.initially_running}")
        if item.started_by_acmm:
            print("      temporary startup: started and restored")
        if isinstance(state, dict):
            package = state.get("package")
            if isinstance(package, dict):
                package_sha256 = package.get("sha256")
                if isinstance(package_sha256, str):
                    print(f"      package SHA-256:   {package_sha256}")
            print(
                "      application scope: "
                f"{state.get('application_scope', 'unknown')}"
            )
        if result.error is not None:
            print(f"      error:             {result.error}")
        elif result.report is not None:
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
                "Experiment cohort context is not fully converged with full "
                "experiment apparatus."
            )
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

    if args.json:
        payload = {
            "stopped": not failed,
            "subjects": [asdict(item) for item in results],
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
