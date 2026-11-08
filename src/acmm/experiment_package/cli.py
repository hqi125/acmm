from __future__ import annotations

import argparse
from pathlib import Path
import sys


from acmm.experiment_package.loader import experiment_package_error, load_experiment_package
from acmm.experiment_package.model import experiment_package
from acmm.experiment_package.staging import install_experiment_package
from acmm.experiment_package.runtime import (
    experiment_runtime_error,
    run_staged_experiment_apparatus_action,
)
from acmm.experiment_package.resources import (
    apply_staged_integration_resources,
    remove_staged_integration_resources,
    remove_superseded_staged_integration_resources,
    validate_staged_package_secrets,
)
from acmm.experiment_package.store import (
    experiment_package_store_error,
    list_installed_experiment_packages,
    resolve_installed_experiment_package,
    uninstall_experiment_package,
)
from acmm_infra.cohort import (
    apply_cohort,
    package_deployment_references,
    resolve_attached_package_cohort,
    resolve_package_cohort,
)
from acmm_infra.common import infrastructure_error
from acmm_infra.docker import docker_error
from acmm_infra.profiles.common import (
    default_repo_root,
    default_runtime_root,
    profile_error,
)


def register_experiment_package_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    package_parser = subparsers.add_parser(
        "package",
        help="Validate and manage installed ACMM Experiment Packages.",
    )
    package_subparsers = package_parser.add_subparsers(
        dest="package_command",
        required=True,
    )

    validate_parser = package_subparsers.add_parser(
        "validate",
        help="Validate an Experiment Package source without installing it.",
    )
    validate_parser.add_argument("source", help="Path to an Experiment Package directory.")
    validate_parser.set_defaults(handler=run_package_validate)

    install_parser = package_subparsers.add_parser(
        "install",
        help="Validate, hash, and install an Experiment Package revision.",
    )
    install_parser.add_argument("source", help="Path to an Experiment Package directory.")
    install_parser.add_argument(
        "--upgrade",
        action="store_true",
        help=(
            "Converge an already-installed experiment ID onto this source revision: "
            "move subjects using older revisions directly to the new revision and purge "
            "all older installed revisions."
        ),
    )
    install_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum provisioning timeout used by --upgrade.",
    )
    install_parser.set_defaults(handler=run_package_install)

    apply_parser = package_subparsers.add_parser(
        "apply",
        help="Apply one installed Experiment Package to its qualified subjects.",
    )
    apply_parser.add_argument(
        "selector",
        help=(
            "Installed package selector: EXPERIMENT_ID when unique, or "
            "EXPERIMENT_ID@SHA_PREFIX when multiple revisions are installed."
        ),
    )
    _add_subject_arguments(apply_parser)
    apply_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum provisioning timeout passed to each subject manager.",
    )
    apply_parser.set_defaults(handler=run_package_apply)

    apparatus_parser = package_subparsers.add_parser(
        "apparatus",
        help="Run one trusted package-owned apparatus lifecycle action.",
    )
    apparatus_parser.add_argument(
        "selector",
        help=(
            "Installed package selector: EXPERIMENT_ID when unique, or "
            "EXPERIMENT_ID@SHA_PREFIX when multiple revisions are installed."
        ),
    )
    apparatus_parser.add_argument(
        "action",
        help="Package-owned apparatus action identifier, for example 'initialize'.",
    )
    apparatus_parser.set_defaults(handler=run_package_apparatus)

    detach_parser = package_subparsers.add_parser(
        "detach",
        help="Detach one installed Experiment Package from subjects using it.",
    )
    detach_parser.add_argument(
        "selector",
        help=(
            "Installed package selector: EXPERIMENT_ID when unique, or "
            "EXPERIMENT_ID@SHA_PREFIX when multiple revisions are installed."
        ),
    )
    _add_subject_arguments(detach_parser)
    detach_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum provisioning timeout passed to each subject manager.",
    )
    detach_parser.set_defaults(handler=run_package_detach)

    uninstall_parser = package_subparsers.add_parser(
        "uninstall",
        help="Remove one installed Experiment Package revision.",
    )
    uninstall_parser.add_argument(
        "selector",
        help=(
            "Installed package selector: EXPERIMENT_ID when unique, or "
            "EXPERIMENT_ID@SHA_PREFIX when multiple revisions are installed."
        ),
    )
    uninstall_parser.add_argument(
        "--detach-if-applied",
        action="store_true",
        help=(
            "Detach this exact package revision from subjects that use it before "
            "removing the installed snapshot."
        ),
    )
    uninstall_parser.set_defaults(handler=run_package_uninstall)

    purge_parser = package_subparsers.add_parser(
        "purge",
        help="Remove every installed Experiment Package revision.",
    )
    purge_parser.add_argument(
        "--detach-all",
        action="store_true",
        help=(
            "Detach every attached package revision from subjects before purging "
            "the installed package store."
        ),
    )
    purge_parser.set_defaults(handler=run_package_purge)

    list_parser = package_subparsers.add_parser(
        "list",
        help="List installed Experiment Package revisions.",
    )
    list_parser.set_defaults(handler=run_package_list)

    show_parser = package_subparsers.add_parser(
        "show",
        help="Validate and show one installed Experiment Package revision.",
    )
    show_parser.add_argument(
        "selector",
        help=(
            "Installed package selector: EXPERIMENT_ID when unique, or "
            "EXPERIMENT_ID@SHA_PREFIX when multiple revisions are installed."
        ),
    )
    show_parser.set_defaults(handler=run_package_show)


def run_package_validate(args: argparse.Namespace) -> int:
    try:
        package = load_experiment_package(
            Path(args.source),
            contract_schema_path=None,
        )
    except (experiment_package_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Experiment Package is valid:")
    _print_package_summary(package, installed=False)
    return 0


def run_package_install(args: argparse.Namespace) -> int:
    if args.upgrade:
        return _run_package_upgrade(args)

    try:
        package = install_experiment_package(
            Path(args.source),
            default_runtime_root().expanduser().resolve(),
            contract_schema_path=None,
        )
    except (experiment_package_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Experiment Package installed:")
    _print_package_summary(package, installed=True)
    print(f"  selector:         {package.experiment_id}@{package.package_sha256[:12]}")
    return 0


def _run_package_upgrade(args: argparse.Namespace) -> int:
    repo_root = default_repo_root().expanduser().resolve()
    runtime_root = default_runtime_root().expanduser().resolve()
    try:
        source_package = load_experiment_package(
            Path(args.source),
            contract_schema_path=None,
        )
        installed_before = tuple(
            item
            for item in list_installed_experiment_packages(runtime_root)
            if item.experiment_id == source_package.experiment_id
        )
        if not installed_before:
            raise experiment_package_store_error(
                "Cannot upgrade Experiment Package "
                f"'{source_package.experiment_id}' because no installed revision exists. "
                "Use 'acmm package install' without '--upgrade' for first installation."
            )

        old_packages: list[experiment_package] = []
        attached_by_revision: list[tuple[experiment_package, tuple[str, ...]]] = []
        for item in installed_before:
            package = resolve_installed_experiment_package(
                runtime_root,
                f"{item.experiment_id}@{item.package_sha256}",
                contract_schema_path=None,
            )
            if package.package_sha256 == source_package.package_sha256:
                continue
            old_packages.append(package)
            attached_subjects = _resolve_package_attachment_instances(
                repo_root,
                runtime_root,
                package,
            )
            attached_subject_ids = tuple(subject.subject_id for subject in attached_subjects)
            attached_by_revision.append((package, attached_subject_ids))

        target = install_experiment_package(
            Path(args.source),
            runtime_root,
            contract_schema_path=None,
        )
        subject_ids = tuple(
            dict.fromkeys(
                subject_id
                for _, attached in attached_by_revision
                for subject_id in attached
            )
        )
        target_cohort = (
            resolve_package_cohort(repo_root, target, subject_ids)
            if subject_ids
            else None
        )
        if subject_ids:
            validate_staged_package_secrets(target)
    except (
        experiment_package_store_error,
        experiment_package_error,
        profile_error,
        infrastructure_error,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Experiment Package upgrade:")
    print(f"  experiment:       {target.experiment_id}")
    print(f"  target SHA-256:   {target.package_sha256}")
    print(f"  prior revisions:  {len(installed_before)}")
    print(f"  subjects to move: {len(subject_ids)}")
    for subject_id in subject_ids:
        print(f"    {subject_id}")

    def resource_progress(integration_id, message) -> None:
        print(f"[integration:{integration_id}] {message}", flush=True)

    moved: tuple[str, ...] = ()
    if target_cohort is not None:
        try:
            apply_staged_integration_resources(
                target,
                runtime_root,
                timeout_seconds=args.timeout_seconds,
                progress=resource_progress,
            )
            results = apply_cohort(
                repo_root,
                runtime_root,
                target_cohort,
                timeout_seconds=args.timeout_seconds,
                progress=_package_upgrade_progress,
            )
            moved = tuple(item.subject_id for item in results)
        except (
            experiment_package_error,
            profile_error,
            infrastructure_error,
            docker_error,
            OSError,
            ValueError,
        ) as exc:
            print(f"ERROR: Upgrade apply failed: {exc}", file=sys.stderr)
            return 2

    try:
        for package in old_packages:
            references = package_deployment_references(
                repo_root,
                runtime_root,
                package.package_sha256,
            )
            if references:
                raise experiment_package_store_error(
                    "Upgrade did not converge: old revision "
                    f"{package.package_sha256[:12]} is still attached to "
                    + ", ".join(references)
                    + "."
                )

        removed: list[str] = []
        for package in old_packages:
            remove_superseded_staged_integration_resources(
                package,
                target,
                runtime_root,
            )
            uninstall_experiment_package(
                runtime_root,
                f"{package.experiment_id}@{package.package_sha256}",
                contract_schema_path=None,
            )
            removed.append(package.package_sha256)
    except (
        experiment_package_store_error,
        experiment_package_error,
        infrastructure_error,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


    print("Experiment Package upgrade complete:")
    print(f"  experiment:       {target.experiment_id}")
    print(f"  active revision:  {target.package_sha256}")
    print(f"  subjects moved:   {len(moved)}")
    print(f"  revisions purged: {len(removed)}")
    for sha256 in removed:
        print(f"    {sha256}")
    if not removed:
        print("  store already converged on the requested revision")
    return 0


def _package_upgrade_progress(index, total, subject, payload) -> None:
    if index is not None:
        print()
        print(f"[upgrade {index}/{total}] {subject.subject_id}")
        print(f"  infrastructure: {subject.infrastructure_subject}")
        print("  provisioning:")
        return
    print(f"    {payload}", flush=True)


def run_package_apparatus(args: argparse.Namespace) -> int:
    runtime_root = default_runtime_root().expanduser().resolve()
    try:
        package = resolve_installed_experiment_package(
            runtime_root,
            args.selector,
            contract_schema_path=None,
        )
        validate_staged_package_secrets(package)
        progress_started = False

        def report_progress(event) -> None:
            nonlocal progress_started
            if not progress_started:
                print("Experiment Package apparatus progress:")
                print(f"  experiment: {package.experiment_id}")
                print(f"  action:     {args.action}")
                progress_started = True
            if event.step is not None and event.total is not None:
                prefix = f"[{event.step}/{event.total}]"
            elif event.step is not None:
                prefix = f"[{event.step}]"
            else:
                prefix = "[-]"
            print(f"  {prefix} {event.message}", flush=True)

        result = run_staged_experiment_apparatus_action(
            package,
            args.action,
            progress=report_progress,
        )
    except (
        experiment_package_store_error,
        experiment_package_error,
        experiment_runtime_error,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if progress_started:
        print()
    print("Experiment Package apparatus action complete:")
    print(f"  experiment: {package.experiment_id}")
    print(f"  package:    {package.package_sha256}")
    print(f"  action:     {args.action}")
    print(f"  changed:    {'yes' if result.changed else 'no'}")
    print(f"  detail:     {result.detail}")
    if result.observations:
        print("  observations:")
        for key in sorted(result.observations):
            print(f"    {key}: {result.observations[key]}")
    return 0


def run_package_apply(args: argparse.Namespace) -> int:
    repo_root = default_repo_root().expanduser().resolve()
    runtime_root = default_runtime_root().expanduser().resolve()
    try:
        package = resolve_installed_experiment_package(
            runtime_root,
            args.selector,
            contract_schema_path=None,
        )
        cohort = resolve_package_cohort(
            repo_root,
            package,
            tuple(args.subjects or ()),
        )
    except (
        experiment_package_store_error,
        experiment_package_error,
        profile_error,
        infrastructure_error,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    def resource_progress(integration_id, message) -> None:
        print(f"[integration:{integration_id}] {message}", flush=True)

    try:
        validate_staged_package_secrets(package)
        resource_results = apply_staged_integration_resources(
            package,
            runtime_root,
            timeout_seconds=args.timeout_seconds,
            progress=resource_progress,
        )
    except (experiment_package_error, infrastructure_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Experiment Package apply:")
    _print_package_summary(package, installed=True)
    if resource_results:
        print("  integration resources:")
        for item in resource_results:
            print(f"    {item.integration_id}: {dict(item.value)}")
    print(f"  subjects:          {len(cohort.subjects)}")
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

    try:
        results = apply_cohort(
            repo_root,
            runtime_root,
            cohort,
            timeout_seconds=args.timeout_seconds,
            progress=progress,
        )
    except (
        profile_error,
        infrastructure_error,
        docker_error,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print()
    print("Experiment Package apply complete:")
    print(f"  subjects applied: {len(results)}/{len(cohort.subjects)}")
    print(f"  experiment:       {package.experiment_id}")
    print(f"  package SHA-256:  {package.package_sha256}")
    print("  deployment contexts:")
    for item in results:
        print(f"    {item.subject_id}:")
        print(f"      package state:     {item.package_state}")
        print(f"      package SHA-256:   {item.package_sha256 or 'none'}")
    return 0


def _package_detach_progress(index, total, subject, payload) -> None:
    if index is not None:
        print()
        print(f"[detach {index}/{total}] {subject.subject_id}")
        print(f"  infrastructure: {subject.infrastructure_subject}")
        print("  provisioning:")
        return
    print(f"    {payload}", flush=True)


def _detach_installed_package(
    package: experiment_package,
    *,
    selected_subject_ids: tuple[str, ...] = (),
    timeout_seconds: float = 120.0,
) -> tuple[str, ...]:
    repo_root = default_repo_root().expanduser().resolve()
    runtime_root = default_runtime_root().expanduser().resolve()
    cohort = resolve_attached_package_cohort(
        repo_root,
        runtime_root,
        package,
        selected_subject_ids,
    )
    if not cohort.subjects:
        return ()

    results = apply_cohort(
        repo_root,
        runtime_root,
        cohort,
        timeout_seconds=timeout_seconds,
        progress=_package_detach_progress,
    )
    if not package_deployment_references(repo_root, runtime_root, package.package_sha256):
        remove_staged_integration_resources(package, runtime_root)
    return tuple(item.subject_id for item in results)


def run_package_detach(args: argparse.Namespace) -> int:
    runtime_root = default_runtime_root().expanduser().resolve()
    try:
        package = resolve_installed_experiment_package(
            runtime_root,
            args.selector,
            contract_schema_path=None,
        )
        detached = _detach_installed_package(
            package,
            selected_subject_ids=tuple(args.subjects or ()),
            timeout_seconds=args.timeout_seconds,
        )
    except (
        experiment_package_store_error,
        experiment_package_error,
        profile_error,
        infrastructure_error,
        docker_error,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print()
    print("Experiment Package detach complete:")
    print(f"  experiment:       {package.experiment_id}")
    print(f"  package SHA-256:  {package.package_sha256}")
    print(f"  subjects detached: {len(detached)}")
    for subject_id in detached:
        print(f"    {subject_id}")
    if not detached:
        print("  no selected subjects were attached to this package revision")
    return 0


def run_package_uninstall(args: argparse.Namespace) -> int:
    runtime_root = default_runtime_root().expanduser().resolve()
    repo_root = default_repo_root().expanduser().resolve()

    try:
        package = resolve_installed_experiment_package(
            runtime_root,
            args.selector,
            contract_schema_path=None,
        )
        references = package_deployment_references(
            repo_root,
            runtime_root,
            package.package_sha256,
        )
        if references and args.detach_if_applied:
            detached = _detach_installed_package(package)
            if detached:
                print("Experiment Package detached before uninstall:")
                for subject_id in detached:
                    print(f"  {subject_id}")
            references = package_deployment_references(
                repo_root,
                runtime_root,
                package.package_sha256,
            )
        if references:
            joined = ", ".join(references)
            raise experiment_package_store_error(
                "Cannot uninstall an Experiment Package while it is deployed to "
                f"subject infrastructure: {joined}. Run 'acmm package detach "
                f"{args.selector}' first, or grant '--detach-if-applied'."
            )
        remove_staged_integration_resources(package, runtime_root)
        installed = uninstall_experiment_package(
            runtime_root,
            args.selector,
            contract_schema_path=None,
        )
    except (
        experiment_package_store_error,
        profile_error,
        infrastructure_error,
        docker_error,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Experiment Package uninstalled:")
    print(f"  experiment:       {installed.experiment_id}")
    print(f"  package SHA-256:  {installed.package_sha256}")
    print(f"  removed root:     {installed.root}")
    return 0


def run_package_purge(args: argparse.Namespace) -> int:
    runtime_root = default_runtime_root().expanduser().resolve()
    repo_root = default_repo_root().expanduser().resolve()
    try:
        installed = list_installed_experiment_packages(runtime_root)
        if not installed:
            print("Experiment Package store is already empty.")
            return 0

        references_by_selector = {
            item.selector: package_deployment_references(
                repo_root,
                runtime_root,
                item.package_sha256,
            )
            for item in installed
        }
        attached = {
            selector: references
            for selector, references in references_by_selector.items()
            if references
        }
        if attached and not args.detach_all:
            details = "; ".join(
                f"{selector} -> {', '.join(references)}"
                for selector, references in attached.items()
            )
            raise experiment_package_store_error(
                "Cannot purge the Experiment Package store while revisions are "
                "attached: " + details + ". Detach them explicitly or grant "
                "'--detach-all'."
            )

        if attached:
            for item in installed:
                if item.selector not in attached:
                    continue
                package = resolve_installed_experiment_package(
                    runtime_root,
                    f"{item.experiment_id}@{item.package_sha256}",
                    contract_schema_path=None,
                )
                detached = _detach_installed_package(package)
                if detached:
                    print(f"Experiment Package detached before purge: {item.selector}")
                    for subject_id in detached:
                        print(f"  {subject_id}")

        for item in installed:
            references = package_deployment_references(
                repo_root,
                runtime_root,
                item.package_sha256,
            )
            if references:
                raise experiment_package_store_error(
                    f"Cannot purge {item.selector}; it remains attached to "
                    + ", ".join(references)
                    + "."
                )

        removed = []
        for item in installed:
            package = resolve_installed_experiment_package(
                runtime_root,
                f"{item.experiment_id}@{item.package_sha256}",
                contract_schema_path=None,
            )
            remove_staged_integration_resources(package, runtime_root)
            removed.append(
                uninstall_experiment_package(
                    runtime_root,
                    f"{item.experiment_id}@{item.package_sha256}",
                    contract_schema_path=None,
                )
            )
    except (
        experiment_package_store_error,
        experiment_package_error,
        profile_error,
        infrastructure_error,
        docker_error,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Experiment Package store purged:")
    print(f"  revisions removed: {len(removed)}")
    for item in removed:
        print(f"  {item.selector}")
    return 0


def run_package_list(args: argparse.Namespace) -> int:
    runtime_root = default_runtime_root().expanduser().resolve()
    repo_root = default_repo_root().expanduser().resolve()
    try:
        installed = list_installed_experiment_packages(runtime_root)
        attachments = {
            item.package_sha256: package_deployment_references(
                repo_root,
                runtime_root,
                item.package_sha256,
            )
            for item in installed
        }
    except (
        experiment_package_store_error,
        infrastructure_error,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Installed Experiment Packages:")
    print(f"  count: {len(installed)}")
    if installed:
        print("  legend: * attached to one or more subject instances")
    for index, item in enumerate(installed, start=1):
        attached = attachments[item.package_sha256]
        marker = "*" if attached else " "
        print(f"  [{index}] {marker} {item.selector}")
        print(f"      package SHA-256: {item.package_sha256}")
        print(f"      root:            {item.root}")
        print(f"      attached:        {', '.join(attached) if attached else 'none'}")
    return 0


def run_package_show(args: argparse.Namespace) -> int:
    runtime_root = default_runtime_root().expanduser().resolve()
    repo_root = default_repo_root().expanduser().resolve()
    try:
        package = resolve_installed_experiment_package(
            runtime_root,
            args.selector,
            contract_schema_path=None,
        )
        attached_subjects = _resolve_package_attachment_instances(
            repo_root,
            runtime_root,
            package,
        )
    except (
        experiment_package_store_error,
        experiment_package_error,
        profile_error,
        infrastructure_error,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Installed Experiment Package:")
    _print_package_summary(package, installed=True)
    print(f"  selector:         {package.experiment_id}@{package.package_sha256[:12]}")
    print(f"  attached instances: {len(attached_subjects)}")
    for subject in attached_subjects:
        print(f"    {subject.subject_id} ({subject.infrastructure_subject})")
    if not attached_subjects:
        print("    none")
    return 0


def _resolve_package_attachment_instances(
    repo_root: Path,
    runtime_root: Path,
    package: experiment_package,
):
    cohort = resolve_attached_package_cohort(
        repo_root,
        runtime_root,
        package,
    )
    raw_references = set(
        package_deployment_references(
            repo_root,
            runtime_root,
            package.package_sha256,
        )
    )
    mapped_references = {
        subject.infrastructure_subject for subject in cohort.subjects
    }
    if raw_references != mapped_references:
        missing = sorted(raw_references - mapped_references)
        unexpected = sorted(mapped_references - raw_references)
        detail: list[str] = []
        if missing:
            detail.append("unmapped deployment references: " + ", ".join(missing))
        if unexpected:
            detail.append("unexpected mapped references: " + ", ".join(unexpected))
        raise experiment_package_store_error(
            "Installed package attachment state cannot be mapped exactly to Contract "
            "subjects" + (": " + "; ".join(detail) if detail else ".")
        )
    return cohort.subjects


def _add_subject_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--subject",
        dest="subjects",
        action="append",
        help=(
            "Restrict the package operation to a qualified Contract subject ID. "
            "Repeat to select multiple subjects."
        ),
    )


def _print_package_summary(package: experiment_package, *, installed: bool) -> None:
    print(f"  experiment:       {package.experiment_id}")
    print(f"  package schema:   {package.schema_version}")
    print(f"  Contract schema:  {package.contract.get('schema_version', 'unknown')}")
    print(f"  package SHA-256:  {package.package_sha256}")
    print(f"  Contract SHA-256: {package.contract_sha256}")
    if installed:
        print(f"  installed root:   {package.root}")
    else:
        print(f"  source root:      {package.root}")

    if package.runtime is None:
        print("  runtime:          none")
    else:
        print(f"  runtime SHA-256:  {package.runtime.sha256}")

    if package.secrets:
        rendered = ", ".join(
            item.secret_id + ("" if item.required else " (optional)")
            for item in package.secrets
        )
        print(f"  package secrets:  {rendered}")
    else:
        print("  package secrets:  none")

    if package.integrations:
        print("  integrations:")
        for component in package.integrations:
            preset = component.preset or "none"
            print(
                f"    {component.integration_id}: {component.sha256} "
                f"(api={component.api_version}, preset={preset})"
            )
            if component.secrets:
                rendered = ", ".join(
                    item.secret_id + ("" if item.required else " (optional)")
                    for item in component.secrets
                )
                print(f"      secret slots: {rendered}")
    else:
        print("  integrations:     none")

    if package.analysis:
        print("  supplementary analysis:")
        for component in package.analysis:
            print(f"    {component.kind}: {component.sha256}")
    else:
        print("  supplementary analysis: none")

    print(f"  Contract warnings: {len(package.contract_warnings)}")
    for warning in package.contract_warnings:
        print(f"    {warning.path}: [{warning.code}] {warning.message}")
