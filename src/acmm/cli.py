from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from acmm.contract.loader import contract_load_error, load_contract
from acmm.contract.validator import load_schema, validate_contract
from acmm.record.loader import load_run_record, run_record_load_error
from acmm.record.validator import (
    load_run_record_schema,
    sha256_file,
    validate_run_record,
)
from acmm.runtime.planner import build_experiment_plan, experiment_plan
from acmm.validation import validation_result


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
    except (OSError, ValueError) as exc:
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


def _run_validate(args: argparse.Namespace) -> int:
    contract_path = Path(args.contract)
    schema_path = (
        Path(args.schema)
        if args.schema is not None
        else _default_contract_schema_path()
    )

    _, result, status = _load_and_validate_contract(
        contract_path,
        schema_path,
    )

    if status != 0:
        return status

    assert result is not None

    print(
        f"Contract is valid with {len(result.warnings)} warning(s): "
        f"{contract_path}"
    )
    return 0


def _format_plan(plan: experiment_plan) -> str:
    headers = ("INDEX", "SUBJECT", "SCENARIO", "REPETITION")
    rows = [
        (
            str(run.run_index),
            run.subject_id,
            run.scenario_id,
            str(run.repetition),
        )
        for run in plan.runs
    ]

    widths = [
        max(
            len(headers[column]),
            *(len(row[column]) for row in rows),
        )
        for column in range(len(headers))
    ]

    def format_row(row: tuple[str, str, str, str]) -> str:
        return "  ".join(
            value.ljust(widths[column])
            for column, value in enumerate(row)
        ).rstrip()

    lines = [
        f"Experiment: {plan.experiment_id}",
        f"Runs: {plan.run_count}",
        "",
        format_row(headers),
        format_row(tuple("-" * width for width in widths)),
    ]

    lines.extend(format_row(row) for row in rows)
    return "\n".join(lines)


def _run_plan(args: argparse.Namespace) -> int:
    contract_path = Path(args.contract)
    schema_path = (
        Path(args.schema)
        if args.schema is not None
        else _default_contract_schema_path()
    )

    contract, _, status = _load_and_validate_contract(
        contract_path,
        schema_path,
    )

    if status != 0:
        return status

    assert contract is not None

    plan = build_experiment_plan(contract)
    print(_format_plan(plan))
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


def _add_contract_schema_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--schema",
        help=(
            "Path to a Contract JSON Schema. "
            "Defaults to contracts/v0_1/contract.schema.json in this repository."
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acmm",
        description="ACMM experiment tooling.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate an ACMM Experiment Contract.",
    )
    validate_parser.add_argument(
        "contract",
        help="Path to an experiment YAML file.",
    )
    _add_contract_schema_argument(validate_parser)
    validate_parser.set_defaults(handler=_run_validate)

    plan_parser = subparsers.add_parser(
        "plan",
        help="Validate and expand an ACMM Experiment Contract into planned runs.",
    )
    plan_parser.add_argument(
        "contract",
        help="Path to an experiment YAML file.",
    )
    _add_contract_schema_argument(plan_parser)
    plan_parser.set_defaults(handler=_run_plan)

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
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
