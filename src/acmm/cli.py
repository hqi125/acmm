from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from acmm.contract.loader import contract_load_error, load_contract
from acmm.contract.validator import (
    validation_result,
    load_schema,
    validate_contract,
)
from acmm.runtime.planner import build_experiment_plan, experiment_plan


def _default_schema_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "v0_1"
        / "contract.schema.json"
    )


def _print_issue(prefix: str, path: str, code: str, message: str) -> None:
    print(f"{prefix}: {path}: [{code}] {message}")


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
            f"ERROR: Could not load schema '{schema_path}': {exc}",
            file=sys.stderr,
        )
        return None, None, 2

    result = validate_contract(contract, schema)

    for issue in result.errors:
        _print_issue("ERROR", issue.path, issue.code, issue.message)

    for issue in result.warnings:
        _print_issue("WARNING", issue.path, issue.code, issue.message)

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
        else _default_schema_path()
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
        format_row(
            tuple("-" * width for width in widths)
        ),
    ]

    lines.extend(format_row(row) for row in rows)
    return "\n".join(lines)


def _run_plan(args: argparse.Namespace) -> int:
    contract_path = Path(args.contract)
    schema_path = (
        Path(args.schema)
        if args.schema is not None
        else _default_schema_path()
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


def _add_schema_argument(parser: argparse.ArgumentParser) -> None:
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
    _add_schema_argument(validate_parser)
    validate_parser.set_defaults(handler=_run_validate)

    plan_parser = subparsers.add_parser(
        "plan",
        help="Validate and expand an ACMM Experiment Contract into planned runs.",
    )
    plan_parser.add_argument(
        "contract",
        help="Path to an experiment YAML file.",
    )
    _add_schema_argument(plan_parser)
    plan_parser.set_defaults(handler=_run_plan)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
