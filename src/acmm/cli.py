from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys
from typing import Any, TextIO

from acmm.analysis.export import analysis_export_error, export_execution
from acmm.contract.compatibility import validate_harness_compatibility
from acmm.contract.loader import contract_load_error, load_contract
from acmm.contract.validator import load_schema, validate_contract
from acmm.deployment_preflight import (
    deployment_preflight_result,
    inspect_package_deployment_contexts,
)
from acmm.experiment_package import (
    compose_execution_registry,
    experiment_package,
    experiment_package_error,
    experiment_package_store_error,
    experiment_runtime_error,
    load_staged_experiment_runtime,
    package_provenance,
    resolve_installed_experiment_package,
)
from acmm.experiment_package.cli import register_experiment_package_commands
from acmm.environment_cli import register_environment_commands
from acmm.live_preflight import (
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
from acmm.runtime.planner import (
    build_experiment_plan,
    experiment_plan,
    planned_run,
)
from acmm.runtime.result_store import result_write_error
from acmm.validation import validation_result
from acmm_infra.cohort import apply_cohort, resolve_package_cohort
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


def _default_contract_schema_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "v0_1"
        / "contract.schema.json"
    )


def _default_run_record_schema_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "records"
        / "v0_1"
        / "run_record.schema.json"
    )


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
    schema_path: Path,
) -> tuple[dict[str, Any] | None, validation_result | None, int]:
    try:
        contract = load_contract(contract_path)
    except contract_load_error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return None, None, 2

    try:
        schema = load_schema(schema_path)
    except (OSError, ValueError, result_write_error) as exc:
        print(
            f"ERROR: Could not load Contract schema '{schema_path}': {exc}",
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



def _parse_run_index_selectors(
    selectors: list[str] | None,
    plan: experiment_plan,
) -> set[int] | None:
    if selectors is None:
        return None

    selected: set[int] = set()
    maximum = plan.run_count - 1

    for selector in selectors:
        if selector.isdecimal():
            start = int(selector)
            end = start
        else:
            parts = selector.split("-")

            if (
                len(parts) != 2
                or not parts[0].isdecimal()
                or not parts[1].isdecimal()
            ):
                raise ValueError(
                    "--run-index must be a non-negative index or inclusive "
                    f"range START-END; got '{selector}'."
                )

            start = int(parts[0])
            end = int(parts[1])

            if start > end:
                raise ValueError(
                    "--run-index range start must not exceed its end; got "
                    f"'{selector}'."
                )

        if start > maximum or end > maximum:
            raise ValueError(
                f"Run selector '{selector}' is outside the Contract plan "
                f"range 0..{maximum}."
            )

        selected.update(range(start, end + 1))

    return selected


def _resolve_selected_runs(
    plan: experiment_plan,
    contract: dict[str, Any],
    run_selectors: list[str] | None,
    subject_selectors: list[str] | None,
) -> tuple[planned_run, ...]:
    selected_indices = _parse_run_index_selectors(run_selectors, plan)
    selected_subjects = set(subject_selectors) if subject_selectors else None

    if selected_subjects is not None:
        contract_subjects = tuple(subject["id"] for subject in contract["subjects"])
        unknown = sorted(selected_subjects - set(contract_subjects))

        if unknown:
            raise ValueError(
                "Unknown --subject selection(s): "
                + ", ".join(unknown)
                + ". Contract subjects: "
                + ", ".join(contract_subjects)
                + "."
            )

    selected = tuple(
        run
        for run in plan.runs
        if (
            selected_indices is None
            or run.run_index in selected_indices
        )
        and (
            selected_subjects is None
            or run.subject_id in selected_subjects
        )
    )

    if not selected:
        raise ValueError(
            "Run selection is empty after applying --run-index and --subject "
            "filters."
        )

    return selected


def _selected_subject_ids(
    selected_runs: tuple[planned_run, ...],
) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()

    for run in selected_runs:
        if run.subject_id not in seen:
            ordered.append(run.subject_id)
            seen.add(run.subject_id)

    return tuple(ordered)


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
        scope = subject.application_scope or "none"
        if (
            subject.package_sha256 is not None
            and subject.expected_apparatus_sha256 is None
            and subject.observed_apparatus_sha256 is None
        ):
            apparatus = "match (none declared)"
        elif subject.expected_apparatus_sha256 is None:
            apparatus = "unavailable"
        elif subject.observed_apparatus_sha256 is None:
            apparatus = "none"
        elif (
            subject.observed_apparatus_sha256
            == subject.expected_apparatus_sha256
        ):
            apparatus = "match"
        else:
            apparatus = "mismatch"

        context = "ready" if subject.is_ready else "not-ready"
        print(f"  {subject.subject_id}:")
        _print_labeled_fields(
            [
                ("context", context),
                ("experiment", experiment),
                ("package SHA-256", subject.package_sha256 or "none"),
                ("application scope", scope),
                ("apparatus", apparatus),
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


def _ensure_runtime_ready(
    *,
    subject_id: str,
    lease: subject_runtime_lease,
    binding: subject_execution_binding,
    repo_root: Path,
    runtime_root: Path,
    startup_timeout_seconds: float,
) -> None:
    print("Runtime acquisition:")
    print(f"  {subject_id}:")
    if lease.is_running:
        _print_labeled_fields([("action", "verifying running runtime")], indent=4)
    else:
        _print_labeled_fields([("action", "starting")], indent=4)
        lease.ensure_started(timeout_seconds=startup_timeout_seconds)

    readiness = inspect_live_readiness(
        repo_root=repo_root,
        runtime_root=runtime_root,
        subject_bindings={subject_id: binding},
    )
    subject = readiness.subjects[0]
    action = "started by ACMM" if lease.started_by_acmm else "already running"
    _print_labeled_fields(
        [
            ("action", action),
            ("readiness", "ready" if subject.is_ready else "not-ready"),
        ],
        indent=4,
    )
    if subject.is_ready:
        return

    if subject.error is not None:
        print(f"ERROR: {subject_id}: {subject.error}", file=sys.stderr)
    for check in subject.failed_checks:
        print(
            f"ERROR: {subject_id}: [{check.name}] {check.detail}",
            file=sys.stderr,
        )
    raise infrastructure_error(
        f"Subject '{subject_id}' did not become live-ready after startup."
    )


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
    subject_ids = _selected_subject_ids(selected_runs)
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
    schema_path: Path,
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


def _run_experiment(args: argparse.Namespace) -> int:
    schema_path = _default_contract_schema_path()
    repo_root = default_repo_root().expanduser().resolve()
    runtime_root = default_runtime_root().expanduser().resolve()

    try:
        package = _resolve_run_package(
            args.experiment,
            runtime_root,
            schema_path,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    contract = dict(package.contract)
    contract_path = package.contract_path

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

    plan = build_experiment_plan(contract)

    try:
        selected_runs = _resolve_selected_runs(
            plan,
            contract,
            args.run_index,
            args.subject,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    selected_indices = tuple(run.run_index for run in selected_runs)
    selected_subject_ids = _selected_subject_ids(selected_runs)
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
                    "--apply-if-needed. The selected subject context would be "
                    "applied before execution and followed by a second strict "
                    "deployment preflight."
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
            "ACMM to apply the selected Experiment Package context before "
            "execution."
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
    ready_subject_ids: set[str] = set()

    if args.start_if_needed:
        try:
            runtime_leases = _discover_runtime_leases(
                repo_root=repo_root,
                runtime_root=runtime_root,
                subject_bindings=subject_bindings,
            )
        except (infrastructure_error, profile_error, docker_error, OSError, ValueError) as exc:
            print(f"ERROR: Could not inspect selected subject runtimes: {exc}", file=sys.stderr)
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
            print(
                "ERROR: Live readiness preflight failed for an already-running "
                "subject; no execution session was created.",
                file=sys.stderr,
            )
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
            print(
                "ERROR: Live readiness preflight failed; no execution session was "
                "created.",
                file=sys.stderr,
            )
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
        runtime_log_since[subject_id] = datetime.now(timezone.utc)

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
            cleanup_errors = _release_runtime_leases(runtime_leases)
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
    overlays = {
        subject_id: runtime_provenance_configuration(
            load_llm_baseline(
                repo_root,
                subject_bindings[subject_id].llm_baseline_id,
            ),
            subject_bindings[subject_id].infrastructure_subject,
        )
        for subject_id in selected_subject_ids
    }
    registry = compose_execution_registry(
        loaded_runtime.runtime,
        subject_adapters,
        subject_configuration_overlays=overlays,
    )

    pacing = contract["run_protocol"].get("pacing", {})
    inter_run_delay = pacing.get("inter_run_delay_seconds", 0)

    if len(selected_runs) > 1:
        print(
            "Inter-run pacing: "
            f"{inter_run_delay:g}s between selected planned runs."
        )

    def session_ready(session_dir: Path) -> None:
        runtime_log_session_dir[0] = session_dir

    cleanup_errors: tuple[str, ...] = ()
    try:
        result = execute_experiment(
            contract=contract,
            contract_path=contract_path,
            registry=registry,
            results_root=Path(args.results_root),
            harness_version=_harness_version(),
            max_attempts_per_run=args.max_attempts_per_run,
            run_indices=selected_indices,
            progress=lambda message: print(message, flush=True),
            before_run=(
                before_planned_run
                if args.start_if_needed or args.capture_runtime_logs
                else None
            ),
            session_ready=session_ready if args.capture_runtime_logs else None,
            package_provenance=_execution_package_provenance(package),
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
        cleanup_errors = _release_runtime_leases(runtime_leases)
        result = exc.result
        _validate_persisted_execution(result, contract)

        try:
            summary_path = _write_interruption_summary(result)
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
        cleanup_errors = _release_runtime_leases(runtime_leases)
        print(f"ERROR: Experiment execution failed: {exc}", file=sys.stderr)
        for error in cleanup_errors:
            print(f"WARNING: Runtime cleanup failed: {error}", file=sys.stderr)
        return 2

    capture_runtime_log(active_subject_id[0])
    cleanup_errors = _release_runtime_leases(runtime_leases)

    validation_status = _validate_persisted_execution(
        result,
        contract,
    )

    if validation_status != 0:
        return validation_status

    try:
        summary_path = _write_execution_summary(result)
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


def _validate_persisted_execution(
    result: experiment_execution_result | experiment_interruption_result,
    contract: dict[str, Any],
) -> int:
    try:
        record_schema = load_run_record_schema(
            _default_run_record_schema_path()
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


def _write_interruption_summary(
    result: experiment_interruption_result,
) -> Path:
    summary_path = result.session_dir / "execution_summary.txt"

    with summary_path.open(
        "x",
        encoding="utf-8",
        newline="\n",
    ) as output:
        print("Experiment execution interrupted by Ctrl+C.", file=output)
        print(f"  session:       {result.session_dir}", file=output)
        print(f"  interruption:  {result.interruption_path}", file=output)
        print(
            f"  started:       {_format_execution_timestamp(result.started_at)}",
            file=output,
        )
        print(
            f"  interrupted:   {_format_execution_timestamp(result.ended_at)}",
            file=output,
        )
        print(
            f"  duration:      {_format_execution_duration(result)}",
            file=output,
        )
        print(
            f"  selected:      {len(result.selected_run_indices)} "
            f"of {result.contract_run_count}",
            file=output,
        )
        print(
            f"  resolved:      {len(result.resolved_run_indices)} "
            f"of {len(result.selected_run_indices)} selected",
            file=output,
        )
        print(
            "  unfinished:    "
            + _compact_integer_ranges(result.unresolved_run_indices),
            file=output,
        )

        if result.interrupted_run_index is not None:
            detail = f"run {result.interrupted_run_index}"

            if result.interrupted_attempt is not None:
                detail += f", attempt {result.interrupted_attempt}"

            print(f"  interrupted at: {detail}", file=output)

        print(
            "  completion manifest: not written; interrupted executions are "
            "not exportable as complete experiments.",
            file=output,
        )
        _write_slot_summaries(result.slots, output)

    return summary_path


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

    print(f"  started:       {_format_execution_timestamp(result.started_at)}")
    print(f"  interrupted:   {_format_execution_timestamp(result.ended_at)}")
    print(f"  duration:      {_format_execution_duration(result)}")
    print("  completion manifest: not written")
    print("  continuation: unsupported; start a new execution instead")


def _write_execution_summary(
    result: experiment_execution_result,
) -> Path:
    summary_path = result.session_dir / "execution_summary.txt"

    with summary_path.open(
        "x",
        encoding="utf-8",
        newline="\n",
    ) as output:
        _write_execution_summary_content(result, output)

    return summary_path


def _write_execution_summary_content(
    result: experiment_execution_result,
    output: TextIO,
) -> None:
    print("Experiment execution persisted successfully.", file=output)
    print(f"  session:   {result.session_dir}", file=output)
    print(f"  manifest:  {result.manifest_path}", file=output)
    print(
        f"  started:   {_format_execution_timestamp(result.started_at)}",
        file=output,
    )
    print(
        f"  ended:     {_format_execution_timestamp(result.ended_at)}",
        file=output,
    )
    print(
        f"  duration:  {_format_execution_duration(result)}",
        file=output,
    )
    print(
        f"  selected:  {len(result.slots)} of {result.contract_run_count}",
        file=output,
    )
    print(
        f"  resolved:  "
        f"{len(result.slots) - len(result.unresolved_run_indices)} "
        f"of {len(result.slots)} selected",
        file=output,
    )

    _write_slot_summaries(result.slots, output)


def _write_slot_summaries(
    slots: tuple[Any, ...],
    output: TextIO,
) -> None:
    for slot in slots:
        print(
            f"  run {slot.planned.run_index}: "
            f"subject={slot.planned.subject_id}, "
            f"scenario={slot.planned.scenario_id}, "
            f"repetition={slot.planned.repetition}",
            file=output,
        )

        if not slot.attempts:
            print("    no persisted attempt", file=output)
            continue

        for attempt in slot.attempts:
            record_path = attempt.result_path / "run_record.yaml"
            record = load_run_record(record_path)

            print(
                f"    attempt {attempt.attempt}: "
                f"validity={attempt.validity}, "
                f"termination={attempt.termination}",
                file=output,
            )
            print(f"      record: {record_path}", file=output)

            failure = record["execution"].get("failure")

            if failure is not None:
                print(
                    f"      failure: {failure['kind']}: "
                    f"{failure['message']}",
                    file=output,
                )

            for judgment in record.get("judgments", []):
                print(
                    f"      judgment {judgment['opportunity_id']}: "
                    f"{judgment['outcome']}",
                    file=output,
                )

            _write_tool_evidence_summary(attempt.result_path, output)


def _write_tool_evidence_summary(
    run_dir: Path,
    output: TextIO,
) -> None:
    tool_calls_path = run_dir / "evidence" / "tool_calls.json"
    final_response_path = run_dir / "evidence" / "final_response.txt"

    if tool_calls_path.is_file():
        try:
            tool_calls = json.loads(tool_calls_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            tool_calls = None

        if isinstance(tool_calls, list):
            names = [
                item.get("name")
                for item in tool_calls
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            ]
            print(
                "      tool calls: "
                + (", ".join(names) if names else "none"),
                file=output,
            )

    if final_response_path.is_file():
        try:
            final_response = final_response_path.read_text(
                encoding="utf-8"
            ).strip()
        except (OSError, UnicodeDecodeError):
            final_response = ""

        if final_response:
            one_line = " ".join(final_response.splitlines())
            print(f"      final response: {one_line}", file=output)


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

    print(f"  started:      {_format_execution_timestamp(result.started_at)}")
    print(f"  ended:        {_format_execution_timestamp(result.ended_at)}")
    print(f"  duration:     {_format_execution_duration(result)}")


def _format_execution_timestamp(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _format_execution_duration(
    result: experiment_execution_result | experiment_interruption_result,
) -> str:
    elapsed = result.ended_at - result.started_at
    total_milliseconds = (
        elapsed.days * 86_400_000
        + elapsed.seconds * 1_000
        + elapsed.microseconds // 1_000
    )
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


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
        else _default_contract_schema_path()
    )
    record_schema_path = (
        Path(args.record_schema)
        if args.record_schema is not None
        else _default_run_record_schema_path()
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
    register_experiment_package_commands(subparsers)

    run_parser = subparsers.add_parser(
        "run",
        help="Execute one installed ACMM Experiment Package.",
    )
    run_parser.add_argument(
        "experiment",
        help=(
            "Installed Experiment Package selector: EXPERIMENT_ID when unique, "
            "or EXPERIMENT_ID@SHA_PREFIX when multiple revisions are installed."
        ),
    )
    run_parser.add_argument(
        "--run-index",
        action="append",
        metavar="INDEX|START-END",
        help=(
            "Select a planned-run index or inclusive range (for example 20-39). "
            "Repeat to combine selectors. Overlaps are deduplicated. Omit to "
            "select all run indices."
        ),
    )
    run_parser.add_argument(
        "--subject",
        action="append",
        metavar="SUBJECT_ID",
        help=(
            "Select a Contract subject (agent) ID. Repeat to select multiple "
            "subjects. When combined with --run-index, filters are intersected."
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
            "For real execution, permit ACMM to reconcile selected subject "
            "experiment context when deployment preflight is not ready. A "
            "second strict deployment preflight must pass before execution."
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
        "--capture-runtime-logs",
        action="store_true",
        help=(
            "Capture sanitized timestamped per-subject container logs into the "
            "execution result directory. Diagnostic capture is best-effort and "
            "is not used for compliance adjudication."
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
        default="results",
        help="Result root directory (default: results).",
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
    run_parser.set_defaults(handler=_run_experiment)

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
        default="exports",
        help="Analysis export root directory (default: exports).",
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
            "Defaults to contracts/v0_1/contract.schema.json."
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
            "Defaults to contracts/v0_1/contract.schema.json in this repository."
        ),
    )
    record_parser.set_defaults(handler=_run_validate_record)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted before an execution could be finalized.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
