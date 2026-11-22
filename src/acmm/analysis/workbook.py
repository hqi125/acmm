"""Human-readable Excel reporting for normalized ACMM analysis outputs."""
from __future__ import annotations

from dataclasses import dataclass
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from acmm.analysis.layout import COMPLIANCE_SCOPE, analysis_data_dir, analysis_data_dir_from_root


WORKBOOK_NAME = "analysis_report.xlsx"

_TABLE_SPECS = (
    ("Population Summary", "population_summary.csv", "PopulationSummaryTable"),
    ("Cell Coverage", "population_cell_coverage.csv", "CellCoverageTable"),
    ("Missing Runs", "population_missing_runs.csv", "MissingRunsTable"),
    ("Requirement Estimates", "requirement_estimates.csv", "RequirementEstimatesTable"),
    ("Requirement Intervals", "requirement_intervals.csv", "RequirementIntervalsTable"),
    ("Dimension Scores", "dimension_scores.csv", "DimensionScoresTable"),
    ("Dimension Intervals", "dimension_intervals.csv", "DimensionIntervalsTable"),
    ("Runs", "runs.csv", "RunsTable"),
    ("Judgments", "judgments.csv", "JudgmentsTable"),
)

_PERCENT_COLUMNS = {
    "p_hat",
    "coverage",
    "confidence_level",
    "p_hat_lower",
    "p_hat_upper",
    "coverage_lower",
    "coverage_upper",
    "s_d",
    "s_d_lower",
    "s_d_upper",
    "availability_fraction",
}

_INTEGER_COLUMNS = {
    "execution_contract_run_count",
    "execution_selected_run_count",
    "execution_resolved_run_count",
    "max_attempts_per_run",
    "run_index",
    "attempt",
    "repetition",
    "evidence_count",
    "judgment_count",
    "compliant",
    "violation",
    "unadjudicable",
    "not_applicable",
    "adjudicable",
    "applicable",
    "total_runs",
    "estimable_runs",
    "applicable_runs",
    "total_scenarios",
    "estimable_scenarios",
    "applicable_scenarios",
    "scenario_clusters",
    "run_clusters",
    "bootstrap_replicates_requested",
    "p_hat_bootstrap_replicates",
    "coverage_bootstrap_replicates",
    "total_requirements",
    "estimable_requirements",
    "missing_requirements",
    "s_d_bootstrap_replicates",
    "selected_planned_runs",
    "available_valid_runs",
    "missing_planned_runs",
    "excluded_attempt_rows",
    "planned_runs",
    "missing_runs",
    "attempts",
}

_FLOAT_COLUMNS = {
    "inter_run_delay_seconds",
}

_BOOLEAN_COLUMNS: set[str] = {"complete_selection"}

_TIMESTAMP_COLUMNS = {
    "started_at",
    "ended_at",
}

_LONG_TEXT_COLUMNS = {
    "subject_configuration_json",
    "environment_configuration_json",
    "evidence_ids_json",
    "detail",
    "failure_message",
    "result_path",
}

_ID_COLUMNS = {
    "execution_id",
    "experiment_id",
    "contract_sha256",
    "run_id",
    "subject_id",
    "scenario_id",
    "opportunity_id",
    "requirement_id",
    "check_id",
}



class analysis_workbook_error(ValueError):
    """Raised when an ACMM analysis workbook cannot be generated safely."""


@dataclass(frozen=True)
class analysis_workbook_result:
    output_path: Path
    sheet_count: int


@dataclass(frozen=True)
class _csv_table:
    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def build_analysis_workbook(
    export_dir: str | Path,
    output_path: str | Path | None = None,
    analysis_root: str | Path | None = None,
) -> analysis_workbook_result:
    """Build a presentation workbook from one completed normalized analysis export."""

    source_dir = Path(export_dir)
    analysis_dir = (
        Path(analysis_root)
        if analysis_root is not None
        else source_dir / "analysis_output"
    )
    data_dir = (
        analysis_data_dir_from_root(analysis_dir, COMPLIANCE_SCOPE)
        if analysis_root is not None
        else analysis_data_dir(source_dir, COMPLIANCE_SCOPE)
    )
    target_path = (
        Path(output_path)
        if output_path is not None
        else analysis_dir / WORKBOOK_NAME
    )

    if not source_dir.is_dir():
        raise analysis_workbook_error(
            f"Analysis export directory does not exist: '{source_dir}'."
        )

    manifest = _load_yaml_mapping(
        source_dir / "export_manifest.yaml",
        "export manifest",
    )

    tables: dict[str, _csv_table] = {}

    for sheet_name, file_name, _ in _TABLE_SPECS:
        path = (
            source_dir / file_name
            if file_name in {"runs.csv", "judgments.csv"}
            else data_dir / file_name
        )
        tables[sheet_name] = _load_csv_table(path)

    try:
        import xlsxwriter
    except ModuleNotFoundError as exc:
        raise analysis_workbook_error(
            "XlsxWriter is required for the Excel report. Reinstall the repository "
            "environment with 'python -m pip install -e \".[test]\"'."
        ) from exc

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f".{target_path.stem}.tmp.xlsx")
    temp_path.unlink(missing_ok=True)

    try:
        workbook = xlsxwriter.Workbook(str(temp_path))
        workbook.set_properties(
            {
                "title": "ACMM Analysis Report",
                "subject": "Agent Compliance Metric Model analysis results",
                "author": "ACMM",
                "comments": (
                    "Presentation workbook generated from normalized ACMM analysis CSVs."
                ),
            }
        )

        formats = _create_formats(workbook)
        overview = workbook.add_worksheet("Overview")

        for sheet_name, _, table_name in _TABLE_SPECS:
            _write_table_sheet(
                workbook,
                sheet_name,
                table_name,
                tables[sheet_name],
                formats,
            )

        _write_metadata_sheet(
            workbook,
            manifest,
            tables,
            formats,
        )
        _write_overview_sheet(
            workbook,
            overview,
            manifest,
            tables,
            formats,
        )
        workbook.close()
        temp_path.replace(target_path)
    except (OSError, ValueError) as exc:
        temp_path.unlink(missing_ok=True)
        raise analysis_workbook_error(
            f"Could not build Excel analysis report '{target_path}': {exc}"
        ) from exc
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise analysis_workbook_error(
            f"Excel analysis report generation failed: {exc}"
        ) from exc

    return analysis_workbook_result(
        output_path=target_path,
        sheet_count=11,
    )


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as exc:
        raise analysis_workbook_error(
            f"Could not read {label} '{path}': {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise analysis_workbook_error(
            f"{label.capitalize()} '{path}' must contain a YAML mapping."
        )

    return value


def _load_csv_table(path: Path) -> _csv_table:
    try:
        with path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)

            if reader.fieldnames is None:
                raise analysis_workbook_error(
                    f"Analysis table '{path}' does not contain a header row."
                )

            headers = tuple(reader.fieldnames)
            rows = tuple(dict(row) for row in reader)
    except OSError as exc:
        raise analysis_workbook_error(
            f"Could not read analysis table '{path}': {exc}"
        ) from exc

    if not headers:
        raise analysis_workbook_error(
            f"Analysis table '{path}' does not contain any columns."
        )

    return _csv_table(headers=headers, rows=rows)


def _create_formats(workbook: Any) -> dict[str, Any]:
    return {
        "title": workbook.add_format(
            {
                "bold": True,
                "font_size": 20,
                "font_color": "#FFFFFF",
                "bg_color": "#1F4E78",
                "align": "left",
                "valign": "vcenter",
            }
        ),
        "section": workbook.add_format(
            {
                "bold": True,
                "font_size": 12,
                "font_color": "#FFFFFF",
                "bg_color": "#5B9BD5",
                "align": "left",
                "valign": "vcenter",
            }
        ),
        "label": workbook.add_format(
            {
                "bold": True,
                "font_color": "#1F1F1F",
                "bg_color": "#D9EAF7",
                "border": 1,
                "border_color": "#B4C7E7",
            }
        ),
        "value": workbook.add_format(
            {
                "border": 1,
                "border_color": "#D9E2F3",
            }
        ),
        "value_wrap": workbook.add_format(
            {
                "border": 1,
                "border_color": "#D9E2F3",
                "text_wrap": True,
                "valign": "top",
            }
        ),
        "integer": workbook.add_format({"num_format": "0"}),
        "number": workbook.add_format({"num_format": "0.000"}),
        "percent": workbook.add_format({"num_format": "0.0%"}),
        "timestamp": workbook.add_format({"num_format": "yyyy-mm-dd hh:mm:ss.000"}),
        "duration": workbook.add_format({"num_format": "0.000"}),
        "note": workbook.add_format(
            {
                "font_color": "#404040",
                "bg_color": "#F2F2F2",
                "border": 1,
                "border_color": "#D9D9D9",
                "text_wrap": True,
                "valign": "top",
            }
        ),
        "kpi": workbook.add_format(
            {
                "bold": True,
                "font_size": 14,
                "font_color": "#1F1F1F",
                "align": "right",
                "border": 1,
                "border_color": "#B4C7E7",
            }
        ),
        "kpi_percent": workbook.add_format(
            {
                "bold": True,
                "font_size": 14,
                "font_color": "#1F1F1F",
                "align": "right",
                "num_format": "0.0%",
                "border": 1,
                "border_color": "#B4C7E7",
            }
        ),
        "header": workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#4472C4",
                "border": 1,
                "border_color": "#D9E2F3",
                "text_wrap": True,
                "valign": "vcenter",
            }
        ),
        "status_good": workbook.add_format(
            {"bg_color": "#E2F0D9", "font_color": "#375623"}
        ),
        "status_bad": workbook.add_format(
            {"bg_color": "#FCE4D6", "font_color": "#9C0006"}
        ),
        "status_warn": workbook.add_format(
            {"bg_color": "#FFF2CC", "font_color": "#7F6000"}
        ),
        "status_neutral": workbook.add_format(
            {"bg_color": "#E7E6E6", "font_color": "#595959"}
        ),
    }


def _write_table_sheet(
    workbook: Any,
    sheet_name: str,
    table_name: str,
    table: _csv_table,
    formats: dict[str, Any],
) -> None:
    worksheet = workbook.add_worksheet(sheet_name)
    headers = list(table.headers)

    if sheet_name == "Runs":
        headers.append("duration_seconds")

    worksheet.freeze_panes(1, 0)
    worksheet.hide_gridlines(2)
    worksheet.set_row(0, 30)

    for row_index, row in enumerate(table.rows, start=1):
        for column_index, header in enumerate(table.headers):
            _write_typed_cell(
                worksheet,
                row_index,
                column_index,
                header,
                row.get(header, ""),
                formats,
            )

        if sheet_name == "Runs":
            start_col = table.headers.index("started_at")
            end_col = table.headers.index("ended_at")
            excel_row = row_index + 1
            start_ref = _cell_ref(excel_row, start_col + 1)
            end_ref = _cell_ref(excel_row, end_col + 1)
            duration = _duration_seconds(
                row.get("started_at", ""),
                row.get("ended_at", ""),
            )
            formula = f"=({end_ref}-{start_ref})*86400"
            worksheet.write_formula(
                row_index,
                len(headers) - 1,
                formula,
                formats["duration"],
                duration,
            )

    last_row = max(1, len(table.rows))
    last_col = len(headers) - 1
    columns = [{"header": header} for header in headers]
    worksheet.add_table(
        0,
        0,
        last_row,
        last_col,
        {
            "name": table_name,
            "columns": columns,
            "style": "Table Style Medium 2",
        },
    )

    _set_sheet_column_widths(worksheet, headers, table)
    _apply_table_conditional_formats(
        worksheet,
        sheet_name,
        headers,
        len(table.rows),
        formats,
    )


def _write_typed_cell(
    worksheet: Any,
    row: int,
    col: int,
    header: str,
    raw_value: str,
    formats: dict[str, Any],
) -> None:
    if raw_value == "":
        worksheet.write_blank(row, col, None)
        return

    if header in _TIMESTAMP_COLUMNS:
        timestamp = _parse_timestamp(raw_value)

        if timestamp is not None:
            worksheet.write_datetime(row, col, timestamp, formats["timestamp"])
            return

    if header in _BOOLEAN_COLUMNS:
        lowered = raw_value.lower()

        if lowered in {"true", "false"}:
            worksheet.write_boolean(row, col, lowered == "true")
            return

    if header in _INTEGER_COLUMNS:
        try:
            worksheet.write_number(row, col, int(raw_value), formats["integer"])
            return
        except ValueError:
            pass

    if header in _PERCENT_COLUMNS:
        try:
            worksheet.write_number(row, col, float(raw_value), formats["percent"])
            return
        except ValueError:
            pass

    if header in _FLOAT_COLUMNS:
        try:
            worksheet.write_number(row, col, float(raw_value), formats["number"])
            return
        except ValueError:
            pass

    worksheet.write_string(row, col, raw_value)


def _set_sheet_column_widths(
    worksheet: Any,
    headers: list[str],
    table: _csv_table,
) -> None:
    for index, header in enumerate(headers):
        if header == "duration_seconds":
            worksheet.set_column(index, index, 16)
            continue

        if header in _LONG_TEXT_COLUMNS:
            width = 38
        elif header in _ID_COLUMNS:
            width = 28
        elif header in _TIMESTAMP_COLUMNS:
            width = 24
        else:
            sample_lengths = [len(header)]

            for row in table.rows[:100]:
                sample_lengths.append(len(row.get(header, "")))

            width = min(max(sample_lengths) + 2, 28)
            width = max(width, 10)

        worksheet.set_column(index, index, width)


def _apply_table_conditional_formats(
    worksheet: Any,
    sheet_name: str,
    headers: list[str],
    row_count: int,
    formats: dict[str, Any],
) -> None:
    if row_count == 0:
        return

    first_data_row = 1
    last_data_row = row_count

    for column_name in _PERCENT_COLUMNS:
        if column_name not in headers:
            continue

        index = headers.index(column_name)
        worksheet.conditional_format(
            first_data_row,
            index,
            last_data_row,
            index,
            {
                "type": "3_color_scale",
                "min_color": "#F8696B",
                "mid_color": "#FFEB84",
                "max_color": "#63BE7B",
            },
        )

    if sheet_name == "Judgments" and "outcome" in headers:
        index = headers.index("outcome")
        for value, format_name in (
            ("compliant", "status_good"),
            ("violation", "status_bad"),
            ("unadjudicable", "status_warn"),
            ("not_applicable", "status_neutral"),
        ):
            worksheet.conditional_format(
                first_data_row,
                index,
                last_data_row,
                index,
                {
                    "type": "text",
                    "criteria": "containing",
                    "value": value,
                    "format": formats[format_name],
                },
            )

    if sheet_name == "Runs":
        for column_name, expected in (
            ("termination", "agent_completed"),
            ("validity", "valid"),
        ):
            if column_name not in headers:
                continue

            index = headers.index(column_name)
            worksheet.conditional_format(
                first_data_row,
                index,
                last_data_row,
                index,
                {
                    "type": "text",
                    "criteria": "not containing",
                    "value": expected,
                    "format": formats["status_bad"],
                },
            )

    for status_name in ("aggregation_status", "p_hat_interval_status", "coverage_interval_status", "s_d_interval_status"):
        if status_name not in headers:
            continue

        index = headers.index(status_name)
        worksheet.conditional_format(
            first_data_row,
            index,
            last_data_row,
            index,
            {
                "type": "text",
                "criteria": "not containing",
                "value": "ok",
                "format": formats["status_warn"],
            },
        )


def _write_metadata_sheet(
    workbook: Any,
    manifest: dict[str, Any],
    tables: dict[str, _csv_table],
    formats: dict[str, Any],
) -> None:
    worksheet = workbook.add_worksheet("Metadata")
    worksheet.hide_gridlines(2)
    worksheet.freeze_panes(1, 0)
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    table_meta = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}
    requirement_intervals = tables["Requirement Intervals"]
    first_interval = requirement_intervals.rows[0] if requirement_intervals.rows else {}
    population_summary = tables["Population Summary"]
    population = population_summary.rows[0] if population_summary.rows else {}

    rows = [
        ("generated_at_utc", datetime.now(timezone.utc).isoformat()),
        ("workbook_generator", "acmm.analysis.workbook"),
        ("export_schema_version", str(manifest.get("schema_version", ""))),
        ("source_kind", str(source.get("kind", "run"))),
        ("execution_id", str(source.get("execution_id", ""))),
        ("campaign_id", str(source.get("campaign_id", ""))),
        ("merge_id", str(source.get("merge_id", ""))),
        ("experiment_id", str(source.get("experiment_id", ""))),
        ("contract_sha256", str(source.get("contract_sha256", ""))),
        ("runs_rows", str(_nested_value(table_meta, "runs", "rows"))),
        ("runs_sha256", str(_nested_value(table_meta, "runs", "sha256"))),
        ("judgments_rows", str(_nested_value(table_meta, "judgments", "rows"))),
        ("judgments_sha256", str(_nested_value(table_meta, "judgments", "sha256"))),
        ("population_policy", population.get("population_policy", "")),
        ("complete_selection", population.get("complete_selection", "")),
        ("selected_planned_runs", population.get("selected_planned_runs", "")),
        ("available_valid_runs", population.get("available_valid_runs", "")),
        ("missing_planned_runs", population.get("missing_planned_runs", "")),
        ("confidence_level", first_interval.get("confidence_level", "")),
        ("interval_method", first_interval.get("interval_method", "")),
        (
            "bootstrap_replicates_requested",
            first_interval.get("bootstrap_replicates_requested", ""),
        ),
        (
            "source_of_truth",
            "Canonical acquisition artifacts remain authoritative; this workbook is a presentation layer over validated export and analysis tables.",
        ),
    ]

    worksheet.write_row(0, 0, ["field", "value"], formats["header"])

    for row_index, (name, value) in enumerate(rows, start=1):
        worksheet.write_string(row_index, 0, name)
        worksheet.write_string(row_index, 1, value, formats["value_wrap"])

    worksheet.add_table(
        0,
        0,
        len(rows),
        1,
        {
            "name": "MetadataTable",
            "columns": [{"header": "field"}, {"header": "value"}],
            "style": "Table Style Medium 2",
        },
    )
    worksheet.set_column(0, 0, 34)
    worksheet.set_column(1, 1, 90)


def _write_overview_sheet(
    workbook: Any,
    worksheet: Any,
    manifest: dict[str, Any],
    tables: dict[str, _csv_table],
    formats: dict[str, Any],
) -> None:
    worksheet.hide_gridlines(2)
    worksheet.set_column("A:A", 24)
    worksheet.set_column("B:B", 22)
    worksheet.set_column("C:C", 3)
    worksheet.set_column("D:D", 28)
    worksheet.set_column("E:H", 15)
    worksheet.set_column("I:I", 3)
    worksheet.set_column("J:Q", 12)
    worksheet.set_row(0, 32)
    worksheet.set_row(1, 16)
    worksheet.merge_range("A1:H2", "ACMM Analysis Report", formats["title"])

    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    experiment_id = str(source.get("experiment_id", ""))
    contract_sha = str(source.get("contract_sha256", ""))

    population_summary = tables["Population Summary"]
    population = population_summary.rows[0] if population_summary.rows else {}
    source_rows: tuple[tuple[str, str], ...]
    if source.get("kind") == "campaign":
        source_rows = (
            ("Campaign", str(source.get("campaign_id", ""))),
            ("Merge", str(source.get("merge_id", ""))),
        )
    else:
        source_rows = (("Execution", str(source.get("execution_id", ""))),)
    metadata_rows = (
        *source_rows,
        ("Experiment", experiment_id),
        ("Contract SHA-256", contract_sha),
        ("Population policy", population.get("population_policy", "")),
        (
            "Available population",
            f"{population.get('available_valid_runs', '')} / "
            f"{population.get('selected_planned_runs', '')} planned",
        ),
        ("Generated (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
    )

    for offset, (label, value) in enumerate(metadata_rows, start=3):
        worksheet.write(offset, 0, label, formats["label"])
        worksheet.merge_range(offset, 1, offset, 7, value, formats["value"])

    worksheet.merge_range("A11:B11", "Run / Judgment Summary", formats["section"])
    summary_specs = (
        ("Resolved valid runs", '=COUNTIF(RunsTable[selected],"true")', _selected_run_count(tables["Runs"]), "kpi"),
        ("Completed runs", '=COUNTIF(RunsTable[termination],"agent_completed")', _run_value_count(tables["Runs"], "termination", "agent_completed"), "kpi"),
        ("Timeouts", '=COUNTIF(RunsTable[termination],"timeout")', _run_value_count(tables["Runs"], "termination", "timeout"), "kpi"),
        ("Valid attempts", '=COUNTIF(RunsTable[validity],"valid")', _run_value_count(tables["Runs"], "validity", "valid"), "kpi"),
        ("Compliant", '=COUNTIF(JudgmentsTable[outcome],"compliant")', _judgment_value_count(tables["Judgments"], "compliant"), "kpi"),
        ("Violations", '=COUNTIF(JudgmentsTable[outcome],"violation")', _judgment_value_count(tables["Judgments"], "violation"), "kpi"),
        ("Unadjudicable", '=COUNTIF(JudgmentsTable[outcome],"unadjudicable")', _judgment_value_count(tables["Judgments"], "unadjudicable"), "kpi"),
        ("Not applicable", '=COUNTIF(JudgmentsTable[outcome],"not_applicable")', _judgment_value_count(tables["Judgments"], "not_applicable"), "kpi"),
    )

    for offset, (label, formula, cached, format_name) in enumerate(summary_specs, start=11):
        worksheet.write(offset, 0, label, formats["label"])
        worksheet.write_formula(offset, 1, formula, formats[format_name], cached)

    subjects = _unique_values(tables["Runs"], "subject_id")
    worksheet.merge_range("A21:B21", "Mean Run Duration by Subject", formats["section"])
    worksheet.write_row(21, 0, ["subject_id", "mean_seconds"], formats["header"])

    for index, subject in enumerate(subjects, start=22):
        worksheet.write_string(index, 0, subject)
        formula = (
            f'=SUMIF(RunsTable[subject_id],A{index + 1},RunsTable[duration_seconds])/'
            f'COUNTIF(RunsTable[subject_id],A{index + 1})'
        )
        cached = _mean_duration_for_subject(tables["Runs"], subject)
        worksheet.write_formula(index, 1, formula, formats["duration"], cached)

    dimensions = _unique_values(tables["Dimension Scores"], "dimension")
    worksheet.merge_range("D11:H11", "Opportunity-Weighted Arithmetic Dimension Scores", formats["section"])
    worksheet.write(11, 3, "subject_id", formats["header"])

    for column, dimension in enumerate(dimensions, start=4):
        worksheet.write(11, column, dimension, formats["header"])

    for row_offset, subject in enumerate(subjects, start=12):
        worksheet.write_string(row_offset, 3, subject)

        for column, dimension in enumerate(dimensions, start=4):
            column_letter = _column_letter(column + 1)
            subject_ref = f"$D{row_offset + 1}"
            dimension_ref = f"{column_letter}$12"
            formula = (
                '=SUMIFS(DimensionScoresTable[s_d],'
                'DimensionScoresTable[estimand],"opportunity",'
                'DimensionScoresTable[aggregation_method],"arithmetic",'
                f'DimensionScoresTable[subject_id],{subject_ref},'
                f'DimensionScoresTable[dimension],{dimension_ref})'
            )
            cached = _dimension_score(
                tables["Dimension Scores"],
                subject,
                dimension,
            )
            worksheet.write_formula(
                row_offset,
                column,
                formula,
                formats["percent"],
                cached,
            )

    note_start = max(27, 23 + len(subjects))
    worksheet.merge_range(
        note_start,
        0,
        note_start,
        7,
        "Interpretation Notes",
        formats["section"],
    )
    notes = (
        "Population policy controls whether incomplete selected plans are rejected (strict) or analyzed using available scientifically valid runs (available-valid). Missing planned runs are missing observations, never automatic violations or compliant outcomes.",
        "Available-valid estimates describe the observed valid population. When missingness is related to subject behavior or condition, interpret pooled estimates cautiously and inspect Cell Coverage / Missing Runs explicitly.",
        "Read compliance estimates together with coverage; perfect compliance can coexist with incomplete adjudication coverage.",
        "A percentile bootstrap interval of [1, 1] at the empirical boundary means no failures appeared in the observed resamples; it is not proof of literal certainty.",
        "Dimension intervals recompute requirement estimates and S_D inside every hierarchical scenario-to-run bootstrap replicate, preserving dependence among requirements.",
        "This workbook is a tabular presentation layer. Selectable figures are rendered by the R analysis modules as external PNG/PDF artifacts; normalized CSV outputs and the export manifest remain the direct analysis artifacts.",
    )

    for offset, note in enumerate(notes, start=note_start + 1):
        worksheet.merge_range(offset, 0, offset, 7, note, formats["note"])
        worksheet.set_row(offset, 32)


def _selected_run_count(table: _csv_table) -> int:
    return sum(row.get("selected", "").lower() == "true" for row in table.rows)


def _run_value_count(table: _csv_table, column: str, value: str) -> int:
    return sum(row.get(column, "") == value for row in table.rows)


def _judgment_value_count(table: _csv_table, value: str) -> int:
    return sum(row.get("outcome", "") == value for row in table.rows)


def _unique_values(table: _csv_table, column: str) -> list[str]:
    return sorted({row.get(column, "") for row in table.rows if row.get(column, "")})


def _mean_duration_for_subject(table: _csv_table, subject: str) -> float:
    values: list[float] = []

    for row in table.rows:
        if row.get("subject_id") != subject:
            continue

        duration = _duration_seconds(
            row.get("started_at", ""),
            row.get("ended_at", ""),
        )

        if duration is not None:
            values.append(duration)

    if not values:
        return 0.0

    return sum(values) / len(values)


def _dimension_score(
    table: _csv_table,
    subject: str,
    dimension: str,
) -> float:
    for row in table.rows:
        if (
            row.get("estimand") == "opportunity"
            and row.get("aggregation_method") == "arithmetic"
            and row.get("subject_id") == subject
            and row.get("dimension") == dimension
        ):
            try:
                return float(row.get("s_d", ""))
            except ValueError:
                return 0.0

    return 0.0


def _duration_seconds(started_at: str, ended_at: str) -> float | None:
    started = _parse_timestamp(started_at)
    ended = _parse_timestamp(ended_at)

    if started is None or ended is None:
        return None

    return (ended - started).total_seconds()


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)

    return parsed


def _nested_value(mapping: dict[str, Any], *keys: str) -> Any:
    value: Any = mapping

    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key, "")

    return value


def _cell_ref(row: int, column: int) -> str:
    return f"{_column_letter(column)}{row}"


def _column_letter(column: int) -> str:
    letters = ""
    value = column

    while value > 0:
        value, remainder = divmod(value - 1, 26)
        letters = chr(ord("A") + remainder) + letters

    return letters
