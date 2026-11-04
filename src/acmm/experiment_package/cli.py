from __future__ import annotations

import argparse
from pathlib import Path
import sys


from acmm.experiment_package.loader import experiment_package_error, load_experiment_package
from acmm.experiment_package.model import experiment_package
from acmm.experiment_package.staging import install_experiment_package
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

    print("Experiment Package apply:")
    _print_package_summary(package, installed=True)
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
        print(f"      application scope: {item.application_scope}")
        print(f"      apparatus:         {item.apparatus_state}")
        print(f"      apparatus SHA-256: {item.source_sha256 or 'none'}")
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
    try:
        package = resolve_installed_experiment_package(
            runtime_root,
            args.selector,
            contract_schema_path=None,
        )
        references = package_deployment_references(
            default_repo_root(),
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
                default_repo_root(),
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


def run_package_list(args: argparse.Namespace) -> int:
    try:
        installed = list_installed_experiment_packages(
            default_runtime_root().expanduser().resolve()
        )
    except (experiment_package_store_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Installed Experiment Packages:")
    print(f"  count: {len(installed)}")
    for item in installed:
        print(f"  {item.selector}")
        print(f"    package SHA-256: {item.package_sha256}")
        print(f"    root:            {item.root}")
    return 0


def run_package_show(args: argparse.Namespace) -> int:
    try:
        package = resolve_installed_experiment_package(
            default_runtime_root().expanduser().resolve(),
            args.selector,
            contract_schema_path=None,
        )
    except (experiment_package_store_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Installed Experiment Package:")
    _print_package_summary(package, installed=True)
    print(f"  selector:         {package.experiment_id}@{package.package_sha256[:12]}")
    return 0


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

    if package.apparatus:
        print("  apparatus:")
        for subject, component in package.apparatus.items():
            print(f"    {subject}: {component.sha256}")
    else:
        print("  apparatus:        none")

    if package.analysis:
        print("  supplementary analysis:")
        for component in package.analysis:
            print(f"    {component.kind}: {component.sha256}")
    else:
        print("  supplementary analysis: none")

    print(f"  Contract warnings: {len(package.contract_warnings)}")
    for warning in package.contract_warnings:
        print(f"    {warning.path}: [{warning.code}] {warning.message}")
