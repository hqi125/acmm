from __future__ import annotations

import argparse
from pathlib import Path
import sys

from acmm.experiment_package.loader import experiment_package_error, load_experiment_package
from acmm.experiment_package.model import experiment_package
from acmm.experiment_package.staging import acquire_experiment_package
from acmm_infra.profiles.common import default_runtime_root


def register_experiment_package_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    package_parser = subparsers.add_parser(
        "package",
        help="Validate and acquire ACMM Experiment Packages.",
    )
    package_subparsers = package_parser.add_subparsers(
        dest="package_command",
        required=True,
    )

    validate_parser = package_subparsers.add_parser(
        "validate",
        help="Validate an Experiment Package without staging it.",
    )
    validate_parser.add_argument("source", help="Path to an Experiment Package directory.")
    _add_contract_schema_argument(validate_parser)
    validate_parser.set_defaults(handler=run_package_validate)

    acquire_parser = package_subparsers.add_parser(
        "acquire",
        help="Validate, hash, and stage an Experiment Package.",
    )
    acquire_parser.add_argument("source", help="Path to an Experiment Package directory.")
    _add_contract_schema_argument(acquire_parser)
    acquire_parser.add_argument(
        "--runtime-root",
        default=str(default_runtime_root()),
        help=(
            "Mutable ACMM runtime root used for immutable package staging. "
            "Defaults to ACMM_RUNTIME_ROOT or ~/acmm_runtime."
        ),
    )
    acquire_parser.set_defaults(handler=run_package_acquire)


def run_package_validate(args: argparse.Namespace) -> int:
    try:
        package = load_experiment_package(
            Path(args.source),
            contract_schema_path=_contract_schema_path(args),
        )
    except (experiment_package_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Experiment Package is valid:")
    _print_package_summary(package, staged=False)
    return 0


def run_package_acquire(args: argparse.Namespace) -> int:
    try:
        package = acquire_experiment_package(
            Path(args.source),
            Path(args.runtime_root),
            contract_schema_path=_contract_schema_path(args),
        )
    except (experiment_package_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Experiment Package acquired:")
    _print_package_summary(package, staged=True)
    return 0


def _contract_schema_path(args: argparse.Namespace) -> Path:
    if args.schema is not None:
        return Path(args.schema)
    return (
        Path(__file__).resolve().parents[3]
        / "contracts"
        / "v0_1"
        / "contract.schema.json"
    )


def _add_contract_schema_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--schema",
        help=(
            "Path to a Contract JSON Schema. Defaults to "
            "contracts/v0_1/contract.schema.json in this repository."
        ),
    )


def _print_package_summary(package: experiment_package, *, staged: bool) -> None:
    print(f"  experiment:       {package.experiment_id}")
    print(f"  schema:           {package.schema_version}")
    print(f"  package SHA-256:  {package.package_sha256}")
    print(f"  Contract SHA-256: {package.contract_sha256}")
    if staged:
        print(f"  staged root:      {package.root}")
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
        print(
            f"    {warning.path}: [{warning.code}] {warning.message}"
        )
