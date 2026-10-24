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
from acmm.experiments.catalog import lookup_experiment_execution_binding
from acmm.subjects.catalog import (
    lookup_subject_execution_binding,
    unsupported_termination_controls,
)
from acmm.record.loader import load_run_record, run_record_load_error
from acmm.record.validator import (
    load_run_record_schema,
    sha256_file,
    validate_run_record,
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


def _print_run_selection_preview(
    plan: experiment_plan,
    selected_runs: tuple[planned_run, ...],
    runtime_descriptions: list[tuple[str, str]],
) -> None:
    subject_ids = _selected_subject_ids(selected_runs)
    run_indices = tuple(run.run_index for run in selected_runs)
    grouped: dict[tuple[str, str], list[planned_run]] = {}

    for run in selected_runs:
        grouped.setdefault((run.subject_id, run.scenario_id), []).append(run)

    print(f"Run selection preview for '{plan.experiment_id}'.")
    print(f"  contract runs: {plan.run_count}")
    print(f"  selected:      {len(selected_runs)}")
    print(f"  subjects:      {', '.join(subject_ids)}")
    print(f"  run indices:   {_compact_integer_ranges(run_indices)}")
    print("  breakdown:")

    for (subject_id, scenario_id), runs in grouped.items():
        indices = tuple(run.run_index for run in runs)
        repetitions = tuple(run.repetition for run in runs)
        print(
            f"    {subject_id} / {scenario_id}: "
            f"runs={_compact_integer_ranges(indices)}; "
            f"repetitions={_compact_integer_ranges(repetitions)}; "
            f"count={len(runs)}"
        )

    print("Execution subject runtime:")
    for subject_id, description in runtime_descriptions:
        print(f"  {subject_id}: {description}")

    print("No execution started (--preview).")

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

    experiment_id = contract["experiment"]["id"]
    binding = lookup_experiment_execution_binding(experiment_id)

    if binding is None:
        print(
            "ERROR: No built-in execution binding is registered for experiment "
            f"'{experiment_id}'.",
            file=sys.stderr,
        )
        return 2

    compatibility = validate_harness_compatibility(
        contract,
        binding.capabilities,
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
    subject_runtime_descriptions: list[tuple[str, str]] = []

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
        subject_runtime_descriptions.append(
            (subject_id, subject_binding.runtime_description)
        )

    if args.preview:
        _print_run_selection_preview(
            plan,
            selected_runs,
            subject_runtime_descriptions,
        )
        return 0

    subject_adapters = {
        subject_id: subject_bindings[subject_id].adapter_builder()
        for subject_id in selected_subject_ids
    }
    registry = binding.registry_builder(subject_adapters)

    if len(selected_runs) == plan.run_count:
        print(
            f"Executing all {plan.run_count} planned run(s) for "
            f"'{plan.experiment_id}'."
        )
    elif len(selected_runs) == 1:
        planned = selected_runs[0]
        print(
            f"Executing run {planned.run_index}/{plan.run_count - 1}: "
            f"subject={planned.subject_id}, scenario={planned.scenario_id}, "
            f"repetition={planned.repetition}."
        )
    else:
        print(
            f"Executing {len(selected_runs)} selected run(s) of "
            f"{plan.run_count} for '{plan.experiment_id}': "
            f"{_compact_integer_ranges(selected_indices)}."
        )

    print("Execution subject runtime:")
    for subject_id, description in subject_runtime_descriptions:
        print(f"  {subject_id}: {description}")

    pacing = contract["run_protocol"].get("pacing", {})
    inter_run_delay = pacing.get("inter_run_delay_seconds", 0)

    if len(selected_runs) > 1:
        print(
            "Inter-run pacing: "
            f"{inter_run_delay:g}s between selected planned runs."
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
    except experiment_execution_interrupted as exc:
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
        return 130
    except (OSError, ValueError) as exc:
        print(f"ERROR: Experiment execution failed: {exc}", file=sys.stderr)
        return 2

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
