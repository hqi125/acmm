"""Excel presentation for the general ACMM availability analysis layer."""
from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path
from typing import Any

import yaml

from acmm.analysis.layout import AVAILABILITY_SCOPE, analysis_data_dir, analysis_data_dir_from_root


AVAILABILITY_WORKBOOK_NAME = "availability_analysis.xlsx"

_TABLES = (
    ("Summary", "availability_summary.csv", "AvailabilitySummary"),
    ("Cells", "availability_cells.csv", "AvailabilityCells"),
    ("Positions", "availability_positions.csv", "AvailabilityPositions"),
    ("Slot Outcomes", "availability_slots.csv", "AvailabilitySlots"),
    ("Failure Modes", "availability_failure_modes.csv", "AvailabilityFailureModes"),
    (
        "Attempt Outcomes",
        "availability_attempt_outcomes.csv",
        "AvailabilityAttemptOutcomes",
    ),
    ("Intervals", "availability_intervals.csv", "AvailabilityIntervals"),
    ("Sensitivity", "availability_sensitivity.csv", "AvailabilitySensitivity"),
)

_PERCENT_COLUMNS = {
    "availability_fraction",
    "first_attempt_availability_fraction",
    "retry_recovery_fraction",
    "fraction_of_scope_unavailable",
    "fraction_of_scope_planned",
    "confidence_level",
    "availability_lower",
    "availability_upper",
    "observed_p_hat",
    "all_missing_violation_lower",
    "all_missing_compliant_upper",
    "sensitivity_width",
}

_INTEGER_COLUMNS = {
    "planned_slots",
    "available_valid_slots",
    "unavailable_slots",
    "first_attempt_valid_slots",
    "retried_slots",
    "recovered_by_retry_slots",
    "total_attempts",
    "invalid_attempts",
    "run_index",
    "repetition",
    "selected_position_within_repetition",
    "attempts_used",
    "max_attempts_per_run",
    "valid_attempt",
    "attempt",
    "attempt_rows",
    "scenario_clusters",
    "bootstrap_replicates_requested",
    "bootstrap_replicates_finite",
    "observed_compliant",
    "observed_violations",
    "observed_unadjudicable",
    "observed_not_applicable",
    "observed_adjudicable",
    "missing_planned_opportunities",
}

_BOOLEAN_COLUMNS = {
    "first_attempt_valid",
    "retry_attempted",
    "recovered_by_retry",
    "available_valid",
}

_LONG_TEXT_COLUMNS = {
    "failure_message",
    "sensitivity_method",
}


class availability_workbook_error(ValueError):
    """Raised when the supplementary availability workbook cannot be built."""


@dataclass(frozen=True)
class availability_workbook_result:
    output_path: Path
    sheet_count: int


@dataclass(frozen=True)
class _csv_table:
    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def build_availability_workbook(
    export_dir: str | Path,
    output_path: str | Path | None = None,
    analysis_root: str | Path | None = None,
) -> availability_workbook_result:
    source_dir = Path(export_dir)
    analysis_dir = (
        Path(analysis_root)
        if analysis_root is not None
        else source_dir / "analysis_output"
    )
    data_dir = (
        analysis_data_dir_from_root(analysis_dir, AVAILABILITY_SCOPE)
        if analysis_root is not None
        else analysis_data_dir(source_dir, AVAILABILITY_SCOPE)
    )
    target_path = (
        Path(output_path)
        if output_path is not None
        else analysis_dir / AVAILABILITY_WORKBOOK_NAME
    )

    manifest = _load_yaml_mapping(source_dir / "export_manifest.yaml", "export manifest")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise availability_workbook_error(
            "Export manifest must define a source mapping."
        )

    tables = {
        sheet_name: _load_csv_table(data_dir / file_name)
        for sheet_name, file_name, _ in _TABLES
    }

    try:
        import xlsxwriter
    except ModuleNotFoundError as exc:
        raise availability_workbook_error(
            "XlsxWriter is required for the availability workbook."
        ) from exc

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f".{target_path.stem}.tmp.xlsx")
    temp_path.unlink(missing_ok=True)

    try:
        workbook = xlsxwriter.Workbook(str(temp_path))
        workbook.set_properties(
            {
                "title": "ACMM Availability Analysis",
                "subject": "Acquisition availability and missing-run sensitivity",
                "author": "ACMM",
                "comments": (
                    "Supplementary workbook generated from normalized ACMM "
                    "availability analysis tables."
                ),
            }
        )
        formats = _create_formats(workbook)
        overview = workbook.add_worksheet("Overview")

        for sheet_name, _, table_name in _TABLES:
            _write_table_sheet(
                workbook,
                sheet_name,
                table_name,
                tables[sheet_name],
                formats,
            )

        _write_overview(overview, source, tables, formats)
        workbook.close()
        temp_path.replace(target_path)
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise availability_workbook_error(
            f"Could not build availability workbook '{target_path}': {exc}"
        ) from exc

    return availability_workbook_result(
        output_path=target_path,
        sheet_count=1 + len(_TABLES),
    )


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as exc:
        raise availability_workbook_error(
            f"Could not read {label} '{path}': {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise availability_workbook_error(
            f"{label.capitalize()} '{path}' must contain a mapping."
        )
    return value


def _load_csv_table(path: Path) -> _csv_table:
    try:
        with path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames is None:
                raise availability_workbook_error(
                    f"Availability table '{path}' has no header row."
                )
            rows = tuple(dict(row) for row in reader)
            return _csv_table(tuple(reader.fieldnames), rows)
    except OSError as exc:
        raise availability_workbook_error(
            f"Could not read availability table '{path}': {exc}"
        ) from exc


def _create_formats(workbook: Any) -> dict[str, Any]:
    return {
        "title": workbook.add_format(
            {
                "bold": True,
                "font_size": 18,
                "font_color": "#FFFFFF",
                "bg_color": "#1F4E78",
            }
        ),
        "section": workbook.add_format(
            {
                "bold": True,
                "font_size": 12,
                "font_color": "#FFFFFF",
                "bg_color": "#5B9BD5",
            }
        ),
        "label": workbook.add_format(
            {
                "bold": True,
                "bg_color": "#D9EAF7",
                "border": 1,
                "border_color": "#B4C7E7",
            }
        ),
        "header": workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#4472C4",
                "text_wrap": True,
                "valign": "top",
            }
        ),
        "percent": workbook.add_format({"num_format": "0.0%"}),
        "integer": workbook.add_format({"num_format": "0"}),
        "number": workbook.add_format({"num_format": "0.000"}),
        "boolean": workbook.add_format({"align": "center"}),
        "note": workbook.add_format(
            {
                "text_wrap": True,
                "valign": "top",
                "bg_color": "#F2F2F2",
                "border": 1,
                "border_color": "#D9D9D9",
            }
        ),
    }


def _write_overview(
    worksheet: Any,
    source: dict[str, Any],
    tables: dict[str, _csv_table],
    formats: dict[str, Any],
) -> None:
    worksheet.hide_gridlines(2)
    worksheet.set_column("A:A", 32)
    worksheet.set_column("B:B", 78)
    worksheet.set_row(0, 28)
    worksheet.write("A1", "ACMM Availability Analysis", formats["title"])
    worksheet.write("A3", "Experiment", formats["label"])
    worksheet.write("B3", str(source.get("experiment_id", "")))
    worksheet.write("A4", "Execution", formats["label"])
    worksheet.write("B4", str(source.get("execution_id", "")))

    summary = tables["Summary"]
    overall = next(
        (row for row in summary.rows if row.get("scope") == "overall"),
        None,
    )
    if overall is not None:
        worksheet.write("A6", "Planned slots", formats["label"])
        worksheet.write("B6", _number_or_text(overall.get("planned_slots", "")))
        worksheet.write("A7", "Available valid slots", formats["label"])
        worksheet.write(
            "B7",
            _number_or_text(overall.get("available_valid_slots", "")),
        )
        worksheet.write("A8", "Availability", formats["label"])
        _write_percent_cell(
            worksheet,
            "B8",
            overall.get("availability_fraction", ""),
            formats["percent"],
        )
        worksheet.write("A9", "First-attempt availability", formats["label"])
        _write_percent_cell(
            worksheet,
            "B9",
            overall.get("first_attempt_availability_fraction", ""),
            formats["percent"],
        )

    worksheet.write("A11", "Measurement model", formats["section"])
    worksheet.merge_range(
        "A12:B15",
        "A planned run slot is scientifically available only when the run protocol "
        "produces one selected valid attempt. Invalid attempts and unresolved slots "
        "remain acquisition failures; they are not converted into compliance "
        "violations. First-attempt availability and retry recovery are reported "
        "separately so the retry protocol remains visible.",
        formats["note"],
    )
    worksheet.write("A17", "Failure interpretation", formats["section"])
    worksheet.merge_range(
        "A18:B21",
        "termination and failure_kind are operational surfaces recorded by the "
        "runtime. They do not by themselves establish causal attribution. For "
        "example, a harness_error can still originate from malformed subject tool "
        "use. Causal diagnosis should remain evidence-backed and separate from the "
        "availability rate.",
        formats["note"],
    )
    worksheet.write("A23", "Sensitivity envelope", formats["section"])
    worksheet.merge_range(
        "A24:B28",
        "For each requirement, the sensitivity table shows deterministic extreme "
        "bounds obtained by treating every missing planned opportunity as a "
        "violation versus treating every missing planned opportunity as compliant. "
        "These are not confidence intervals and do not assume missing-at-random. "
        "They quantify how much missing acquisition could change the observed "
        "opportunity-weighted compliance estimate.",
        formats["note"],
    )


def _write_table_sheet(
    workbook: Any,
    sheet_name: str,
    table_name: str,
    table: _csv_table,
    formats: dict[str, Any],
) -> None:
    worksheet = workbook.add_worksheet(sheet_name)
    worksheet.hide_gridlines(2)
    if not table.headers:
        return

    for column, header in enumerate(table.headers):
        worksheet.write(0, column, header, formats["header"])

    for row_index, row in enumerate(table.rows, start=1):
        for column, header in enumerate(table.headers):
            value = row.get(header, "")
            _write_typed_value(
                worksheet,
                row_index,
                column,
                header,
                value,
                formats,
            )

    last_row = max(1, len(table.rows))
    last_column = len(table.headers) - 1
    worksheet.add_table(
        0,
        0,
        last_row,
        last_column,
        {
            "name": table_name,
            "columns": [{"header": header} for header in table.headers],
        },
    )
    worksheet.freeze_panes(1, 0)

    for column, header in enumerate(table.headers):
        if header in _LONG_TEXT_COLUMNS:
            width = 54
        elif header in {"failure_kind", "interval_method", "sensitivity_status"}:
            width = 34
        else:
            width = min(max(len(header) + 2, 12), 28)
        worksheet.set_column(column, column, width)


def _write_typed_value(
    worksheet: Any,
    row: int,
    column: int,
    header: str,
    value: str,
    formats: dict[str, Any],
) -> None:
    if value == "":
        worksheet.write_blank(row, column, None)
        return
    if header in _PERCENT_COLUMNS:
        try:
            worksheet.write_number(row, column, float(value), formats["percent"])
            return
        except ValueError:
            pass
    if header in _INTEGER_COLUMNS:
        try:
            worksheet.write_number(row, column, int(float(value)), formats["integer"])
            return
        except ValueError:
            pass
    if header == "mean_attempts_per_slot":
        try:
            worksheet.write_number(row, column, float(value), formats["number"])
            return
        except ValueError:
            pass
    if header in _BOOLEAN_COLUMNS and value.lower() in {"true", "false"}:
        worksheet.write_boolean(row, column, value.lower() == "true", formats["boolean"])
        return
    if header in _LONG_TEXT_COLUMNS:
        worksheet.write(row, column, value, formats["note"])
        return
    worksheet.write(row, column, value)


def _write_percent_cell(
    worksheet: Any,
    cell: str,
    value: str,
    cell_format: Any,
) -> None:
    try:
        worksheet.write_number(cell, float(value), cell_format)
    except (TypeError, ValueError):
        worksheet.write(cell, value)


def _number_or_text(value: str) -> int | float | str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else number
