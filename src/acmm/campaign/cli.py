from __future__ import annotations

import argparse
from pathlib import Path
import sys

from acmm.experiment_package import (
    experiment_package_error,
    experiment_package_store_error,
    resolve_installed_experiment_package,
)
from acmm.campaign.model import (
    build_campaign_preview,
    campaign_error,
    create_campaign,
    default_generated_root,
    ensure_campaign_package_installed,
    load_campaign,
)
from acmm.campaign.merge import create_campaign_merge
from acmm.campaign.recovery import reconcile_campaign
from acmm.campaign.execution import (
    campaign_execution_lock,
    create_campaign_execution_context,
    eligible_campaign_runs,
    inspect_campaign_status,
    order_campaign_runs_for_execution,
    select_campaign_runs,
)
from acmm_infra.profiles.common import default_runtime_root


def register_campaign_commands(subparsers: argparse._SubParsersAction) -> None:
    campaign_parser = subparsers.add_parser(
        "campaign",
        help="Create, execute, inspect, and merge durable frozen experiment campaigns.",
    )
    commands = campaign_parser.add_subparsers(
        dest="campaign_command",
        metavar="COMMAND",
        required=True,
    )

    create_parser = commands.add_parser(
        "create",
        help="Freeze one installed Experiment Package into a durable campaign.",
    )
    create_parser.add_argument(
        "experiment",
        help="Installed Experiment Package selector to freeze.",
    )
    create_parser.add_argument(
        "--preview",
        action="store_true",
        help="Validate and show the campaign design without creating a campaign.",
    )
    create_parser.add_argument(
        "--generated-root",
        default=str(default_generated_root()),
        help=(
            "Generated artifact root for durable campaigns "
            f"(default: {default_generated_root()})."
        ),
    )
    create_parser.set_defaults(handler=_campaign_create)

    execute_parser = commands.add_parser(
        "execute",
        help="Execute eligible logical runs from one frozen campaign.",
    )
    execute_parser.add_argument(
        "campaign_id",
        help="Campaign ID returned by 'acmm campaign create'.",
    )
    execute_run_index_group = execute_parser.add_mutually_exclusive_group()
    execute_run_index_group.add_argument(
        "--run-index",
        action="append",
        metavar="INDEX|START-END",
        help=(
            "Select a frozen campaign run index or inclusive range. Repeat to "
            "combine selectors."
        ),
    )
    execute_run_index_group.add_argument(
        "--subject-run-index",
        action="append",
        metavar="INDEX|START-END",
        help=(
            "Select a zero-based run index relative to each selected --subject. "
            "Requires --subject."
        ),
    )
    execute_parser.add_argument(
        "--subject",
        action="append",
        metavar="SUBJECT_ID",
        help="Filter the frozen campaign by subject ID. Repeat to combine.",
    )
    execute_parser.add_argument(
        "--scenario",
        action="append",
        metavar="SCENARIO_ID",
        help="Filter the frozen campaign by scenario ID. Repeat to combine.",
    )
    execute_parser.add_argument(
        "--repetition",
        action="append",
        metavar="N|START-END",
        help="Filter by frozen 1-based repetition number or inclusive range.",
    )
    execute_parser.add_argument(
        "--apply-if-needed",
        action="store_true",
        help=(
            "Permit reconciliation of selected subject deployment context before "
            "campaign execution."
        ),
    )
    execute_parser.add_argument(
        "--start-if-needed",
        action="store_true",
        help="Permit ACMM to start selected subject runtimes when needed.",
    )
    execute_parser.add_argument(
        "--prestart-selected-subjects",
        action="store_true",
        help=(
            "With --start-if-needed, acquire every selected subject before the "
            "execution session is created."
        ),
    )
    execute_parser.add_argument(
        "--capture-runtime-logs",
        action="store_true",
        help="Capture sanitized per-subject runtime logs into the campaign execution.",
    )
    execute_parser.add_argument(
        "--capture-environment-snapshots",
        action="store_true",
        help=(
            "Ask the Experiment Package lifecycle to capture diagnostic pre/post "
            "environment snapshots for each run. This may add provider traffic and is "
            "intended for validation/debugging rather than final scientific acquisition."
        ),
    )
    execute_parser.add_argument(
        "--startup-max-attempts",
        type=int,
        default=3,
        help="Maximum runtime acquisition attempts for each selected subject.",
    )
    execute_parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum startup wait for a subject started by ACMM.",
    )
    execute_parser.add_argument(
        "--max-attempts-per-run",
        type=int,
        default=1,
        help="Maximum attempts for an invalid execution of a selected logical run.",
    )
    execute_parser.add_argument(
        "--generated-root",
        default=str(default_generated_root()),
        help=(
            "Generated artifact root containing campaigns "
            f"(default: {default_generated_root()})."
        ),
    )
    execute_parser.set_defaults(handler=_campaign_execute)

    status_parser = commands.add_parser(
        "status",
        help="Inspect one durable campaign without mutating it.",
    )
    status_parser.add_argument(
        "campaign_id",
        help="Campaign ID returned by 'acmm campaign create'.",
    )
    status_parser.add_argument(
        "--generated-root",
        default=str(default_generated_root()),
        help=(
            "Generated artifact root containing campaigns "
            f"(default: {default_generated_root()})."
        ),
    )
    status_parser.set_defaults(handler=_campaign_status)

    merge_parser = commands.add_parser(
        "merge",
        help="Assemble accepted campaign observations into one immutable merged product.",
    )
    merge_parser.add_argument(
        "campaign_id",
        help="Campaign ID returned by 'acmm campaign create'.",
    )
    merge_parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Permit a visibly partial merge containing only currently observed "
            "logical runs."
        ),
    )
    merge_parser.add_argument(
        "--generated-root",
        default=str(default_generated_root()),
        help=(
            "Generated artifact root containing campaigns "
            f"(default: {default_generated_root()})."
        ),
    )
    merge_parser.set_defaults(handler=_campaign_merge)


def _campaign_create(args: argparse.Namespace) -> int:
    runtime_root = default_runtime_root().expanduser().resolve()
    try:
        package = resolve_installed_experiment_package(
            runtime_root,
            args.experiment,
        )
        preview = build_campaign_preview(package)
    except (
        campaign_error,
        experiment_package_error,
        experiment_package_store_error,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    _print_preview(preview)
    if args.preview:
        print("No campaign created (--preview).")
        return 0

    try:
        artifact = create_campaign(
            package,
            Path(args.generated_root),
        )
    except (campaign_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Campaign created:")
    print(f"  id:              {artifact.campaign_id}")
    print(f"  experiment:      {artifact.experiment_id}")
    print(f"  package:         {artifact.manifest['package']['sha256']}")
    print(f"  design digest:   {artifact.manifest['campaign']['design_digest']}")
    print(f"  logical runs:    {artifact.manifest['campaign']['run_count']}")
    print(f"  manifest:        {artifact.manifest_path}")
    return 0


def _campaign_execute(args: argparse.Namespace) -> int:
    try:
        artifact = load_campaign(
            args.campaign_id,
            Path(args.generated_root),
        )
        with campaign_execution_lock(artifact):
            return _campaign_execute_locked(args, artifact)
    except KeyboardInterrupt:
        print(
            "Campaign execution interrupted before the immediate execution boundary; "
            "the campaign lock was released and the next execute will reconcile any "
            "durable recovery state.",
            file=sys.stderr,
        )
        return 130
    except (campaign_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _campaign_execute_locked(args: argparse.Namespace, artifact) -> int:
    reconciliation = reconcile_campaign(artifact)
    selected = select_campaign_runs(
        artifact,
        run_selectors=args.run_index,
        subject_selectors=args.subject,
        subject_run_selectors=args.subject_run_index,
        scenario_selectors=args.scenario,
        repetition_selectors=args.repetition,
    )
    status = inspect_campaign_status(artifact)
    eligible = eligible_campaign_runs(artifact, selected, status)
    eligible = order_campaign_runs_for_execution(artifact, eligible)

    if reconciliation.locally_reconciled_execution_ids:
        print(
            "Campaign reconciliation: finalized unfinished execution(s): "
            + ", ".join(reconciliation.locally_reconciled_execution_ids)
        )
    if reconciliation.externally_recovered_execution_ids:
        print(
            "Campaign recovery: reconciled external state for execution(s): "
            + ", ".join(reconciliation.externally_recovered_execution_ids)
        )

    selected_ids = {run.run_index for run in selected}
    logical_by_index = {
        item["run_index"]: item["run_id"]
        for item in artifact.manifest["runs"]
    }
    observed = set(status.observed_run_ids)
    blocked = set(status.blocked_run_ids)
    active = set(status.active_run_ids)

    print("Campaign execution selection:")
    print(f"  campaign:          {artifact.campaign_id}")
    print(f"  matched:           {len(selected)}")
    print(f"  eligible pending:  {len(eligible)}")
    print(
        "  already observed: "
        + str(
            sum(
                1
                for index in selected_ids
                if logical_by_index[index] in observed
            )
        )
    )
    print(
        "  blocked:          "
        + str(
            sum(
                1
                for index in selected_ids
                if logical_by_index[index] in blocked
            )
        )
    )
    print(
        "  active:           "
        + str(
            sum(
                1
                for index in selected_ids
                if logical_by_index[index] in active
            )
        )
    )

    if not eligible:
        print("No eligible pending logical runs require execution.")
        return 0

    runtime_root = default_runtime_root().expanduser().resolve()
    ensure_campaign_package_installed(artifact, runtime_root)
    context = create_campaign_execution_context(artifact, eligible)

    run_args = argparse.Namespace(**vars(args))
    run_args.experiment = (
        f"{artifact.experiment_id}@{artifact.manifest['package']['sha256']}"
    )
    run_args.preview = False
    run_args.results_root = str(context.session_parent)

    # Selection has already been resolved against the frozen campaign. The
    # immediate runner receives the exact materialized logical runs through the
    # campaign context rather than rebuilding them from the Contract.
    run_args.run_index = None
    run_args.subject_run_index = None
    run_args.subject = None
    run_args.scenario = None
    run_args.repetition = None

    from acmm.cli import _run_experiment

    return _run_experiment(run_args, campaign_context=context)


def _campaign_status(args: argparse.Namespace) -> int:
    try:
        artifact = load_campaign(
            args.campaign_id,
            Path(args.generated_root),
        )
    except (campaign_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        snapshot = inspect_campaign_status(artifact)
    except (campaign_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    manifest = artifact.manifest
    campaign = manifest["campaign"]
    print("Campaign status:")
    print(f"  id:              {campaign['id']}")
    print(f"  experiment:      {campaign['experiment_id']}")
    print(f"  package:         {manifest['package']['sha256']}")
    print(f"  design digest:   {campaign['design_digest']}")
    print(f"  created at:      {campaign['created_at']}")
    print("Runs:")
    print(f"  total:           {snapshot.runs.total}")
    print(f"  pending:         {snapshot.runs.pending}")
    print(f"  active:          {snapshot.runs.active}")
    print(f"  observed:        {snapshot.runs.observed}")
    print(f"  blocked:         {snapshot.runs.blocked}")
    print("Executions:")
    print(f"  total:           {snapshot.executions.total}")
    print(f"  completed:       {snapshot.executions.completed}")
    print(f"  interrupted:     {snapshot.executions.interrupted}")
    print(f"  failed:          {snapshot.executions.failed}")
    print(f"  active:          {snapshot.executions.active}")
    print("Recovery:")
    print(f"  required:        {snapshot.recovery.required}")
    print(f"  recovered:       {snapshot.recovery.recovered}")
    print(f"  failed:          {snapshot.recovery.failed}")
    return 0


def _campaign_merge(args: argparse.Namespace) -> int:
    try:
        artifact = load_campaign(
            args.campaign_id,
            Path(args.generated_root),
        )
        with campaign_execution_lock(artifact):
            merged = create_campaign_merge(
                artifact,
                allow_partial=bool(args.allow_partial),
            )
    except (campaign_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    population = merged.manifest["population"]
    print("Campaign merge created:")
    print(f"  id:              {merged.merge_id}")
    print(f"  campaign:        {artifact.campaign_id}")
    print(f"  completeness:    {merged.manifest['merge']['completeness']}")
    print(f"  observed:        {population['observed']}/{population['total']}")
    print(f"  pending:         {population['pending']}")
    print(f"  blocked:         {population['blocked']}")
    print(f"  manifest:        {merged.manifest_path}")
    print(f"  runs:            {merged.path / 'runs'}")
    return 0


def _print_preview(preview) -> None:
    print("Campaign preview:")
    print(f"  experiment:      {preview.experiment_id}")
    print(f"  package:         {preview.package_sha256}")
    print(f"  design digest:   {preview.design_digest}")
    print(f"  subjects:        {len(preview.subject_ids)}")
    print(f"  scenarios:       {len(preview.scenario_ids)}")
    print(f"  repetitions:     {preview.repetitions}")
    print(f"  logical runs:    {preview.run_count}")
    print(
        "  materialization: "
        + ",".join(preview.materialization_group_by)
        + f" ({preview.materialization_count} slice(s))"
    )


__all__ = ["register_campaign_commands"]
