"""Excel presentation for registered interaction analysis outputs."""
from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path
from typing import Any

import yaml

from acmm.analysis.layout import INTERACTION_SCOPE, analysis_data_dir


INTERACTION_WORKBOOK_NAME = "interaction_analysis.xlsx"

_TABLES = (
    ("Cell Estimates", "interaction_cell_estimates.csv", "InteractionCellEstimates"),
    ("Simple Effects", "interaction_simple_effects.csv", "InteractionSimpleEffects"),
    ("Interactions", "interaction_contrasts.csv", "InteractionContrasts"),
    ("Population", "interaction_population_summary.csv", "InteractionPopulation"),
    (
        "Cell Coverage",
        "interaction_population_cell_coverage.csv",
        "InteractionCellCoverage",
    ),
    (
        "Missing Runs",
        "interaction_population_missing_runs.csv",
        "InteractionMissingRuns",
    ),
)

_PERCENT_COLUMNS = {
    "p_hat",
    "coverage",
    "low_rate",
    "high_rate",
    "risk_difference",
    "p_low_low",
    "p_low_high",
    "p_high_low",
    "p_high_high",
    "effect_factor_b_at_factor_a_low",
    "effect_factor_b_at_factor_a_high",
    "interaction_risk_difference",
    "interaction_lower",
    "interaction_upper",
    "confidence_level",
    "availability_fraction",
}

_INTEGER_COLUMNS = {
    "total_runs",
    "estimable_runs",
    "applicable_runs",
    "repetition_blocks",
    "bootstrap_replicates_requested",
    "bootstrap_replicates_finite",
    "selected_planned_runs",
    "available_valid_runs",
    "missing_planned_runs",
    "excluded_attempt_rows",
    "planned_runs",
    "missing_runs",
    "run_index",
    "repetition",
    "attempts",
}


class interaction_workbook_error(ValueError):
    """Raised when the supplementary interaction workbook cannot be built."""


@dataclass(frozen=True)
class interaction_workbook_result:
    output_path: Path
    sheet_count: int


@dataclass(frozen=True)
class _csv_table:
    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def build_interaction_workbook(
    export_dir: str | Path,
    output_path: str | Path | None = None,
    design_path: str | Path | None = None,
) -> interaction_workbook_result:
    source_dir = Path(export_dir)
    analysis_dir = source_dir / "analysis_output"
    data_dir = analysis_data_dir(source_dir, INTERACTION_SCOPE)
    target_path = (
        Path(output_path)
        if output_path is not None
        else analysis_dir / INTERACTION_WORKBOOK_NAME
    )

    manifest = _load_yaml_mapping(source_dir / "export_manifest.yaml", "export manifest")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise interaction_workbook_error(
            "Export manifest must define a source mapping."
        )

    experiment_id = source.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise interaction_workbook_error(
            "Export manifest must define a non-empty source.experiment_id."
        )
    if design_path is None:
        raise interaction_workbook_error(
            "Interaction workbook requires a registered interaction design path."
        )
    design = _load_interaction_design(Path(design_path), experiment_id)

    tables = {
        sheet_name: _load_csv_table(data_dir / file_name)
        for sheet_name, file_name, _ in _TABLES
    }
    figure_manifest = _load_csv_table(data_dir / "interaction_figures.csv")

    try:
        import xlsxwriter
    except ModuleNotFoundError as exc:
        raise interaction_workbook_error(
            "XlsxWriter is required for the interaction workbook."
        ) from exc

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f".{target_path.stem}.tmp.xlsx")
    temp_path.unlink(missing_ok=True)

    try:
        workbook = xlsxwriter.Workbook(str(temp_path))
        workbook.set_properties(
            {
                "title": "ACMM Interaction Analysis",
                "subject": "Registered cross-dimension interaction analysis",
                "author": "ACMM",
                "comments": (
                    "Supplementary workbook generated from R interaction analysis "
                    "tables and figures."
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

        _write_overview(overview, source, design, tables, formats)
        _write_figures_sheet(workbook, analysis_dir, figure_manifest, formats)
        workbook.close()
        temp_path.replace(target_path)
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise interaction_workbook_error(
            f"Could not build interaction workbook '{target_path}': {exc}"
        ) from exc

    return interaction_workbook_result(output_path=target_path, sheet_count=8)


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as exc:
        raise interaction_workbook_error(
            f"Could not read {label} '{path}': {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise interaction_workbook_error(
            f"{label.capitalize()} '{path}' must contain a mapping."
        )
    return value


def _load_csv_table(path: Path) -> _csv_table:
    try:
        with path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames is None:
                raise interaction_workbook_error(
                    f"Analysis table '{path}' has no header row."
                )
            rows = tuple(dict(row) for row in reader)
            return _csv_table(tuple(reader.fieldnames), rows)
    except OSError as exc:
        raise interaction_workbook_error(
            f"Could not read interaction table '{path}': {exc}"
        ) from exc


def _load_interaction_design(path: Path, experiment_id: str) -> dict[str, Any]:
    design = _load_yaml_mapping(path, "interaction design")
    if design.get("analysis_kind") != "interaction":
        raise interaction_workbook_error(
            "Interaction design analysis_kind must be 'interaction'."
        )
    if design.get("experiment_id") != experiment_id:
        raise interaction_workbook_error(
            "Interaction design experiment_id does not match the export manifest."
        )

    factors = design.get("factors")
    if not isinstance(factors, dict) or len(factors) != 2:
        raise interaction_workbook_error(
            "Interaction design must define exactly two factors."
        )
    for factor_name, factor in factors.items():
        if not isinstance(factor, dict):
            raise interaction_workbook_error(
                f"Interaction factor '{factor_name}' must be a mapping."
            )
        for field in ("display_name", "low_label", "high_label"):
            value = factor.get(field)
            if not isinstance(value, str) or not value:
                raise interaction_workbook_error(
                    f"Interaction factor '{factor_name}' requires non-empty '{field}'."
                )

    estimand = design.get("estimand")
    if not isinstance(estimand, dict):
        raise interaction_workbook_error(
            "Interaction design must define an estimand mapping."
        )
    for field in ("primary_contrast", "interpretation"):
        value = estimand.get(field)
        if not isinstance(value, str) or not value:
            raise interaction_workbook_error(
                f"Interaction estimand requires non-empty '{field}'."
            )
    return design


def _factor_summary(name: str, factor: dict[str, Any]) -> str:
    display_name = str(factor.get("display_name", name))
    return (
        f"{display_name}: {factor['low_label']} -> {factor['high_label']}"
    )


def _create_formats(workbook: Any) -> dict[str, Any]:
    return {
        "title": workbook.add_format({"bold": True, "font_size": 18}),
        "section": workbook.add_format({"bold": True, "font_size": 12}),
        "label": workbook.add_format({"bold": True}),
        "header": workbook.add_format(
            {"bold": True, "text_wrap": True, "valign": "top"}
        ),
        "percent": workbook.add_format({"num_format": "0.0%"}),
        "integer": workbook.add_format({"num_format": "0"}),
        "note": workbook.add_format({"text_wrap": True, "valign": "top"}),
    }


def _write_overview(
    worksheet: Any,
    source: dict[str, Any],
    design: dict[str, Any],
    tables: dict[str, _csv_table],
    formats: dict[str, Any],
) -> None:
    factors = list(design["factors"].items())
    factor_a_name, factor_a = factors[0]
    factor_b_name, factor_b = factors[1]
    estimand = design["estimand"]

    worksheet.set_column("A:A", 31)
    worksheet.set_column("B:B", 72)
    worksheet.write("A1", "ACMM Interaction Analysis", formats["title"])
    worksheet.write("A3", "Experiment", formats["label"])
    worksheet.write("B3", str(source.get("experiment_id", "")))
    worksheet.write("A4", "Execution", formats["label"])
    worksheet.write("B4", str(source.get("execution_id", "")))
    worksheet.write("A6", "Factor A", formats["label"])
    worksheet.write(
        "B6",
        _factor_summary(factor_a_name, factor_a),
    )
    worksheet.write("A7", "Factor B", formats["label"])
    worksheet.write(
        "B7",
        _factor_summary(factor_b_name, factor_b),
    )
    worksheet.write("A9", "Primary contrast", formats["label"])
    worksheet.write(
        "B9",
        str(estimand["primary_contrast"]),
        formats["note"],
    )
    worksheet.write("A11", "Interpretation", formats["section"])
    worksheet.merge_range(
        "A12:B15",
        str(estimand["interpretation"]),
        formats["note"],
    )

    population = tables["Population"]
    if population.rows:
        row = population.rows[0]
        worksheet.write("A17", "Population policy", formats["label"])
        worksheet.write("B17", row.get("population_policy", ""))
        worksheet.write("A18", "Available valid runs", formats["label"])
        worksheet.write("B18", _number_or_text(row.get("available_valid_runs", "")))
        worksheet.write("A19", "Selected planned runs", formats["label"])
        worksheet.write("B19", _number_or_text(row.get("selected_planned_runs", "")))


def _write_table_sheet(
    workbook: Any,
    sheet_name: str,
    table_name: str,
    table: _csv_table,
    formats: dict[str, Any],
) -> None:
    worksheet = workbook.add_worksheet(sheet_name)
    if not table.headers:
        return

    for column, header in enumerate(table.headers):
        worksheet.write(0, column, header, formats["header"])

    for row_index, row in enumerate(table.rows, start=1):
        for column, header in enumerate(table.headers):
            value = row.get(header, "")
            _write_typed_value(worksheet, row_index, column, header, value, formats)

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
        width = min(max(len(header) + 2, 12), 32)
        worksheet.set_column(column, column, width)


def _write_figures_sheet(
    workbook: Any,
    analysis_dir: Path,
    manifest: _csv_table,
    formats: dict[str, Any],
) -> None:
    worksheet = workbook.add_worksheet("Figures")
    worksheet.set_column("A:A", 24)
    worksheet.set_column("B:B", 110)
    worksheet.write("A1", "R-generated interaction figures", formats["title"])
    row = 2

    for figure in manifest.rows:
        title = figure.get("title", figure.get("figure_name", "Figure"))
        status = figure.get("status", "")
        relative_path = figure.get("relative_path", "")
        worksheet.write(row, 0, title, formats["section"])
        worksheet.write(row + 1, 0, f"Status: {status}")
        path = analysis_dir / relative_path
        if status == "generated" and path.is_file():
            worksheet.insert_image(row + 2, 0, str(path), {"x_scale": 0.55, "y_scale": 0.55})
            row += 28
        else:
            worksheet.write(row + 2, 0, "Figure was not generated for this dataset.")
            row += 5


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
    worksheet.write(row, column, value)


def _number_or_text(value: str) -> int | float | str:
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number
