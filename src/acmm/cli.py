from __future__ import annotations

import argparse
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys
from typing import Any

from acmm.adapters.hermes import hermes_adapter_config
from acmm.contract.compatibility import validate_harness_compatibility
from acmm.contract.loader import contract_load_error, load_contract
from acmm.contract.validator import load_schema, validate_contract
from acmm.pilots.magic_number import (
    build_magic_number_hermes_registry,
    magic_number_harness_capabilities,
)
from acmm.record.loader import load_run_record, run_record_load_error
from acmm.record.validator import (
    load_run_record_schema,
    sha256_file,
    validate_run_record,
)
from acmm.runtime.experiment_execution import (
    execute_experiment,
    experiment_execution_result,
)
from acmm.runtime.planner import build_experiment_plan, experiment_plan
from acmm.runtime.result_store import result_write_error
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


def _harness_version() -> str:
    try:
        return version("acmm")
    except PackageNotFoundError:
        return "0.1.0"


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


def _run_experiment(args: argparse.Namespace) -> int:
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

    if contract["experiment"]["id"] != "magic_number_tool_use_v1":
        print(
            "ERROR: No built-in execution binding is registered for experiment "
            f"'{contract['experiment']['id']}'.",
            file=sys.stderr,
        )
        return 2

    compatibility = validate_harness_compatibility(
        contract,
        magic_number_harness_capabilities(),
    )
    _print_validation_result(compatibility)

    if not compatibility.is_valid:
        print(
            "Harness compatibility validation failed with "
            f"{len(compatibility.errors)} error(s)."
        )
        return 1

    plan = build_experiment_plan(contract)
    selected_indices: tuple[int, ...] | None = None

    if args.run_index is not None:
        if args.run_index < 0 or args.run_index >= plan.run_count:
            print(
                f"ERROR: Run index {args.run_index} is outside the Contract plan "
                f"range 0..{plan.run_count - 1}.",
                file=sys.stderr,
            )
            return 2

        selected_indices = (args.run_index,)

    adapter_config = hermes_adapter_config(
        docker_container=args.hermes_container,
        executable="/opt/hermes/bin/hermes",
    )
    registry = build_magic_number_hermes_registry(adapter_config)

    if selected_indices is None:
        print(
            f"Executing all {plan.run_count} planned run(s) for "
            f"'{plan.experiment_id}'."
        )
    else:
        planned = plan.runs[args.run_index]
        print(
            f"Executing run {planned.run_index}/{plan.run_count - 1}: "
            f"subject={planned.subject_id}, scenario={planned.scenario_id}, "
            f"repetition={planned.repetition}."
        )

    print(
        f"Hermes container: {args.hermes_container}; "
        "toolset overlay: acmm."
    )
    pacing = contract["run_protocol"].get("pacing", {})
    inter_run_delay = pacing.get("inter_run_delay_seconds", 0)

    if selected_indices is None and plan.run_count > 1:
        print(
            "Inter-run pacing: "
            f"{inter_run_delay:g}s between planned runs."
        )

    print(
        "Hermes runs in quiet one-shot mode; after it returns, ACMM exports "
        "that exact session to capture tool calls/results as structured evidence."
    )

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
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: Experiment execution failed: {exc}", file=sys.stderr)
        return 2

    validation_status = _validate_persisted_execution(
        result,
        contract,
    )

    if validation_status != 0:
        return validation_status

    _print_execution_summary(result)

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
    result: experiment_execution_result,
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


def _print_execution_summary(
    result: experiment_execution_result,
) -> None:
    print("")
    print("Experiment execution persisted successfully.")
    print(f"  session:  {result.session_dir}")
    print(f"  manifest: {result.manifest_path}")

    for slot in result.slots:
        print(
            f"  run {slot.planned.run_index}: "
            f"subject={slot.planned.subject_id}, "
            f"scenario={slot.planned.scenario_id}, "
            f"repetition={slot.planned.repetition}"
        )

        for attempt in slot.attempts:
            record_path = attempt.result_path / "run_record.yaml"
            record = load_run_record(record_path)

            print(
                f"    attempt {attempt.attempt}: "
                f"validity={attempt.validity}, "
                f"termination={attempt.termination}"
            )
            print(f"      record: {record_path}")

            failure = record["execution"].get("failure")

            if failure is not None:
                print(
                    f"      failure: {failure['kind']}: "
                    f"{failure['message']}"
                )

            for judgment in record.get("judgments", []):
                print(
                    f"      judgment {judgment['opportunity_id']}: "
                    f"{judgment['outcome']}"
                )

            _print_magic_number_evidence_summary(attempt.result_path)


def _print_magic_number_evidence_summary(run_dir: Path) -> None:
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
                + (", ".join(names) if names else "none")
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
            print(f"      final response: {one_line}")


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

    run_parser = subparsers.add_parser(
        "run",
        help=(
            "Execute a validated ACMM Contract using a registered built-in runtime."
        ),
    )
    run_parser.add_argument(
        "contract",
        help="Path to an experiment YAML file.",
    )
    _add_contract_schema_argument(run_parser)
    run_parser.add_argument(
        "--run-index",
        type=int,
        help=(
            "Execute only one deterministic planned-run index. "
            "Omit to execute the complete Contract plan."
        ),
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
    run_parser.add_argument(
        "--hermes-container",
        default="acmm-hermes",
        help=(
            "Running Hermes Docker container name "
            "(default: acmm-hermes)."
        ),
    )
    run_parser.set_defaults(handler=_run_experiment)

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
