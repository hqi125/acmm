from __future__ import annotations

import base64
import csv
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import yaml

from acmm.analysis.interaction_workbook import build_interaction_workbook


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zq1sAAAAASUVORK5CYII="
)


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_fixture(export_dir: Path) -> Path:
    analysis_root = export_dir / "analysis_output"
    analysis_dir = analysis_root / "data" / "interaction"
    export_dir.mkdir(parents=True)
    manifest = {
        "schema_version": "0.1",
        "source": {
            "execution_id": "11111111-1111-4111-8111-111111111111",
            "experiment_id": "example_factorial_v1",
            "contract_sha256": "a" * 64,
        },
    }
    (export_dir / "export_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    design = {
        "schema_version": "0.1",
        "analysis_kind": "interaction",
        "experiment_id": "example_factorial_v1",
        "factors": {
            "ordinary_write_outcome": {
                "display_name": "ordinary write outcome",
                "json_key": "ordinary_write_mode",
                "low_value": "success",
                "high_value": "failure",
                "low_label": "success",
                "high_label": "failure",
            },
            "authorized_recovery": {
                "display_name": "authorized recovery",
                "json_key": "authorized_recovery_available",
                "low_value": False,
                "high_value": True,
                "low_label": "unavailable",
                "high_label": "available",
            },
        },
        "estimand": {
            "primary_contrast": "generic difference-in-differences contrast",
            "interpretation": "Generic registered interaction-design fixture.",
        },
    }
    design_path = export_dir / "interaction_design.yaml"
    design_path.write_text(
        yaml.safe_dump(design, sort_keys=False),
        encoding="utf-8",
    )

    _write_csv(
        analysis_dir / "interaction_cell_estimates.csv",
        [
            "subject_id",
            "requirement_id",
            "dimension",
            "ordinary_write_outcome",
            "authorized_recovery",
            "total_runs",
            "estimable_runs",
            "applicable_runs",
            "p_hat",
            "coverage",
        ],
        [
            {
                "subject_id": "subject_a",
                "requirement_id": "avoid_force",
                "dimension": "boundary",
                "ordinary_write_outcome": "success",
                "authorized_recovery": "unavailable",
                "total_runs": 15,
                "estimable_runs": 15,
                "applicable_runs": 15,
                "p_hat": 1.0,
                "coverage": 1.0,
            }
        ],
    )
    _write_csv(
        analysis_dir / "interaction_simple_effects.csv",
        [
            "subject_id",
            "requirement_id",
            "dimension",
            "effect_factor",
            "stratum_factor",
            "stratum_level",
            "low_rate",
            "high_rate",
            "risk_difference",
            "status",
        ],
        [
            {
                "subject_id": "subject_a",
                "requirement_id": "avoid_force",
                "dimension": "boundary",
                "effect_factor": "authorized_recovery",
                "stratum_factor": "ordinary_write_outcome",
                "stratum_level": "failure",
                "low_rate": 0.8,
                "high_rate": 1.0,
                "risk_difference": 0.2,
                "status": "ok",
            }
        ],
    )
    _write_csv(
        analysis_dir / "interaction_contrasts.csv",
        [
            "subject_id",
            "requirement_id",
            "dimension",
            "interaction_risk_difference",
            "confidence_level",
            "interaction_lower",
            "interaction_upper",
            "interval_status",
        ],
        [
            {
                "subject_id": "subject_a",
                "requirement_id": "avoid_force",
                "dimension": "boundary",
                "interaction_risk_difference": 0.2,
                "confidence_level": 0.95,
                "interaction_lower": -0.1,
                "interaction_upper": 0.4,
                "interval_status": "ok",
            }
        ],
    )
    _write_csv(
        analysis_dir / "interaction_population_summary.csv",
        [
            "population_policy",
            "complete_selection",
            "selected_planned_runs",
            "available_valid_runs",
            "missing_planned_runs",
            "availability_fraction",
            "excluded_attempt_rows",
        ],
        [
            {
                "population_policy": "strict",
                "complete_selection": "true",
                "selected_planned_runs": 180,
                "available_valid_runs": 180,
                "missing_planned_runs": 0,
                "availability_fraction": 1.0,
                "excluded_attempt_rows": 0,
            }
        ],
    )
    _write_csv(
        analysis_dir / "interaction_population_cell_coverage.csv",
        [
            "subject_id",
            "scenario_id",
            "planned_runs",
            "available_valid_runs",
            "missing_runs",
            "availability_fraction",
        ],
        [
            {
                "subject_id": "subject_a",
                "scenario_id": "scenario_a",
                "planned_runs": 15,
                "available_valid_runs": 15,
                "missing_runs": 0,
                "availability_fraction": 1.0,
            }
        ],
    )
    _write_csv(
        analysis_dir / "interaction_population_missing_runs.csv",
        [
            "run_index",
            "subject_id",
            "scenario_id",
            "repetition",
            "attempts",
            "final_termination",
            "final_validity",
            "failure_kind",
            "failure_message",
        ],
        [],
    )

    figure_path = analysis_root / "figures" / "interaction" / "boundary_cell_rates.png"
    figure_path.parent.mkdir(parents=True)
    figure_path.write_bytes(_PNG_1X1)
    _write_csv(
        analysis_dir / "interaction_figures.csv",
        ["figure_name", "title", "relative_path", "status"],
        [
            {
                "figure_name": "boundary_cell_rates",
                "title": "Boundary cell rates",
                "relative_path": "figures/interaction/boundary_cell_rates.png",
                "status": "generated",
            }
        ],
    )
    return design_path


def test_build_interaction_workbook_creates_separate_report(tmp_path: Path) -> None:
    export_dir = tmp_path / "execution_example"
    design_path = _write_fixture(export_dir)
    output_path = export_dir / "analysis_output" / "interaction_analysis.xlsx"

    result = build_interaction_workbook(export_dir, output_path, design_path)

    assert result.output_path == output_path
    assert result.sheet_count == 8
    assert output_path.is_file()
    assert output_path.stat().st_size > 8_000

    with zipfile.ZipFile(output_path) as archive:
        workbook_xml = archive.read("xl/workbook.xml")
        shared_strings = archive.read("xl/sharedStrings.xml").decode("utf-8")
        image_names = [name for name in archive.namelist() if name.startswith("xl/media/")]

    root = ET.fromstring(workbook_xml)
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    names = [
        item.attrib["name"]
        for item in root.findall("main:sheets/main:sheet", namespace)
    ]

    assert names == [
        "Overview",
        "Cell Estimates",
        "Simple Effects",
        "Interactions",
        "Population",
        "Cell Coverage",
        "Missing Runs",
        "Figures",
    ]
    assert "ACMM Interaction Analysis" in shared_strings
    assert "Boundary cell rates" in shared_strings
    assert image_names
