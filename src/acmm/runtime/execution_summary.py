from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, TextIO

from acmm.record.loader import load_run_record
from acmm.runtime.experiment_execution import (
    experiment_execution_result,
    experiment_interruption_result,
)


def write_interruption_summary(
    result: experiment_interruption_result,
) -> Path:
    """Persist the human-readable summary for an interrupted execution."""

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
            f"  started:       {format_execution_timestamp(result.started_at)}",
            file=output,
        )
        print(
            f"  interrupted:   {format_execution_timestamp(result.ended_at)}",
            file=output,
        )
        print(
            f"  duration:      {format_execution_duration(result)}",
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


def write_execution_summary(
    result: experiment_execution_result,
) -> Path:
    """Persist the human-readable summary for a completed execution."""

    summary_path = result.session_dir / "execution_summary.txt"

    with summary_path.open(
        "x",
        encoding="utf-8",
        newline="\n",
    ) as output:
        _write_execution_summary_content(result, output)

    return summary_path


def format_execution_timestamp(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_execution_duration(
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


def _write_execution_summary_content(
    result: experiment_execution_result,
    output: TextIO,
) -> None:
    print("Experiment execution persisted successfully.", file=output)
    print(f"  session:   {result.session_dir}", file=output)
    print(f"  manifest:  {result.manifest_path}", file=output)
    print(
        f"  started:   {format_execution_timestamp(result.started_at)}",
        file=output,
    )
    print(
        f"  ended:     {format_execution_timestamp(result.ended_at)}",
        file=output,
    )
    print(
        f"  duration:  {format_execution_duration(result)}",
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
