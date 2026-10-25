from __future__ import annotations

import argparse
from dataclasses import asdict
from functools import wraps
import json
from pathlib import Path
import sys

from acmm_infra.cohort import (
    apply_cohort,
    doctor_cohort,
    inspect_cohort,
    resolve_baseline_cohort,
    resolve_experiment_cohort,
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
        help="Prepare and inspect ACMM experiment environments.",
    )
    env_subparsers = env_parser.add_subparsers(
        dest="env_command",
        required=True,
    )
    _register_environment_subcommands(env_subparsers)


def _register_environment_subcommands(
    subparsers: argparse._SubParsersAction,
) -> None:
    apply_parser = subparsers.add_parser(
        "apply",
        help="Reconcile an ACMM experiment environment or qualified baseline.",
    )
    apply_parser.add_argument(
        "--experiment",
        dest="experiment_id",
        help=(
            "Prepare the subjects covered by the canonical Contract for this "
            "experiment. Without this option, reconcile qualified baselines."
        ),
    )
    _add_environment_root_arguments(apply_parser)
    _add_subject_arguments(apply_parser)
    apply_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum provisioning timeout passed to each subject manager.",
    )
    apply_parser.set_defaults(handler=_safe_environment_handler(run_environment_apply))

    status_parser = subparsers.add_parser(
        "status",
        help="Show the discovered state of qualified ACMM subject deployments.",
    )
    _add_environment_root_arguments(status_parser)
    _add_subject_arguments(status_parser)
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=_safe_environment_handler(run_environment_status))

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check live readiness of the current ACMM experiment environment.",
    )
    _add_environment_root_arguments(doctor_parser)
    _add_subject_arguments(doctor_parser)
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
    _add_environment_root_arguments(stop_parser)
    _add_subject_arguments(stop_parser)
    stop_parser.add_argument("--json", action="store_true")
    stop_parser.set_defaults(handler=_safe_environment_handler(run_environment_stop))


def run_environment_apply(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).expanduser().resolve()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    selected = tuple(args.subjects or ())

    if args.experiment_id is None:
        print(
            "WARNING: no experiment selected; applying experiment-independent "
            "baselines to the selected cohort and removing active experiment "
            "apparatus.",
            file=sys.stderr,
        )
        cohort = resolve_baseline_cohort(selected)
    else:
        cohort = resolve_experiment_cohort(
            repo_root,
            args.experiment_id,
            selected,
        )

    print("ACMM environment apply:")
    print(f"  experiment:         {cohort.experiment_id or 'baseline'}")
    if cohort.canonical_contract is not None:
        print(f"  canonical Contract: {cohort.canonical_contract}")
    print(f"  subjects:           {len(cohort.subjects)}")
    for subject in cohort.subjects:
        print(f"    {subject.subject_id} ({subject.infrastructure_subject})")

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
    print("Environment apply complete:")
    print(f"  subjects applied: {len(results)}/{len(cohort.subjects)}")
    print(f"  experiment:       {cohort.experiment_id or 'baseline'}")
    if cohort.experiment_id is not None:
        print("  deployment contexts:")
        for item in results:
            print(f"    {item.subject_id}:")
            print(f"      application scope: {item.application_scope}")
            print(f"      apparatus:         {item.apparatus_state}")
            print(f"      apparatus SHA-256: {item.source_sha256}")
    return 0


def run_environment_status(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).expanduser().resolve()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    cohort = resolve_baseline_cohort(tuple(args.subjects or ()))
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
    print("  experiment context:")
    print(f"    state:      {context.state}")
    print(f"    experiment: {context.experiment_id or 'none'}")
    print("  subjects:")
    for item in statuses:
        print(f"    {item.subject_id}:")
        print(f"      infrastructure:    {item.infrastructure_subject}")
        if item.error is not None:
            print(f"      state:             unavailable")
            print(f"      error:             {item.error}")
            continue
        print(f"      running:           {item.running}")
        print(f"      experiment:        {item.experiment_id or 'none'}")
        print(f"      application scope: {item.application_scope or 'none'}")
        print(f"      apparatus:         {item.apparatus_state}")
        if item.source_sha256 is not None:
            print(f"      apparatus SHA-256: {item.source_sha256}")
    return 0


def run_environment_doctor(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).expanduser().resolve()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    cohort = resolve_baseline_cohort(tuple(args.subjects or ()))
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
    print("  experiment context:")
    print(f"    state:      {context.state}")
    print(f"    experiment: {context.experiment_id or 'none'}")
    print("  subjects:")
    for item in doctor_results:
        subject = item.subject
        result = item.doctor
        state = item.deployment_state
        print(f"    {subject.subject_id}:")
        print(f"      infrastructure:    {subject.infrastructure_subject}")
        print(f"      readiness:         {'READY' if item.is_ready else 'NOT READY'}")
        print(f"      experiment:        {result.experiment_id or 'none'}")
        if item.initially_running is not None:
            print(f"      initially running: {item.initially_running}")
        if item.started_by_acmm:
            print("      temporary startup: started and restored")
        if isinstance(state, dict):
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

    print()
    if not context_ready:
        print(
            "Experiment cohort context is not fully converged with full "
            "experiment apparatus."
        )
    print("READY" if ready else "NOT READY")
    return 0 if ready else 1


def run_environment_stop(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).expanduser().resolve()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    cohort = resolve_baseline_cohort(tuple(args.subjects or ()))
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


def _add_environment_root_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo-root",
        default=str(default_repo_root()),
        help="ACMM repository root.",
    )
    parser.add_argument(
        "--runtime-root",
        default=str(default_runtime_root()),
        help="Mutable ACMM runtime root. Defaults to ~/acmm_runtime.",
    )


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
