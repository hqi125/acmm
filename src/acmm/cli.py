from __future__ import annotations

import argparse
from pathlib import Path
import sys

from acmm.contract.loader import contract_load_error, load_contract
from acmm.contract.validator import load_schema, validate_contract


def _default_schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "contracts" / "v0_1" / "contract.schema.json"


def _print_issue(prefix: str, path: str, code: str, message: str) -> None:
    print(f"{prefix}: {path}: [{code}] {message}")


def _run_validate(args: argparse.Namespace) -> int:
    contract_path = Path(args.contract)
    schema_path = Path(args.schema) if args.schema is not None else _default_schema_path()

    try:
        contract = load_contract(contract_path)
    except contract_load_error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        schema = load_schema(schema_path)
    except (OSError, ValueError) as exc:
        print(f"ERROR: Could not load schema '{schema_path}': {exc}", file=sys.stderr)
        return 2

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
        return 1

    print(
        f"Contract is valid with {len(result.warnings)} warning(s): "
        f"{contract_path}"
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acmm",
        description="ACMM experiment tooling.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate an ACMM Experiment Contract.",
    )
    validate_parser.add_argument(
        "contract",
        help="Path to an experiment YAML file.",
    )
    validate_parser.add_argument(
        "--schema",
        help=(
            "Path to a Contract JSON Schema. "
            "Defaults to contracts/v0_1/contract.schema.json in this repository."
        ),
    )
    validate_parser.set_defaults(handler=_run_validate)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
