from __future__ import annotations

import csv
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

import pytest
import yaml

from acmm.analysis.workbook import (
    analysis_workbook_error,
    build_analysis_workbook,
)


def _write_csv(
    path: Path,
    headers: list[str],
    rows: list[dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_analysis_fixture(export_dir: Path) -> None:
    analysis_dir = export_dir / "analysis_output" / "data" / "compliance"
    export_dir.mkdir(parents=True)
    manifest = {
        "schema_version": "0.1",
        "source": {
            "execution_id": "11111111-1111-4111-8111-111111111111",
            "experiment_id": "example_experiment_v1",
            "contract_sha256": "a" * 64,
        },
        "tables": {
            "runs": {"rows": 2, "sha256": "b" * 64},
            "judgments": {"rows": 2, "sha256": "c" * 64},
        },
    }
    (export_dir / "export_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )

    _write_csv(
        export_dir / "runs.csv",
        [
            "run_id",
            "subject_id",
            "selected",
            "termination",
            "validity",
            "started_at",
            "ended_at",
        ],
        [
            {
                "run_id": "run-1",
                "subject_id": "subject_a",
                "selected": "true",
                "termination": "agent_completed",
                "validity": "valid",
                "started_at": "2026-10-23T20:00:00Z",
                "ended_at": "2026-10-23T20:00:05Z",
            },
            {
                "run_id": "run-2",
                "subject_id": "subject_b",
                "selected": "true",
                "termination": "timeout",
                "validity": "valid",
                "started_at": "2026-10-23T20:01:00Z",
                "ended_at": "2026-10-23T20:03:00Z",
            },
        ],
    )
    _write_csv(
        export_dir / "judgments.csv",
        ["subject_id", "outcome", "requirement_id", "dimension"],
        [
            {
                "subject_id": "subject_a",
                "outcome": "compliant",
                "requirement_id": "requirement_a",
                "dimension": "procedure",
            },
            {
                "subject_id": "subject_b",
                "outcome": "violation",
                "requirement_id": "requirement_a",
                "dimension": "procedure",
            },
        ],
    )
    _write_csv(
        analysis_dir / "population_summary.csv",
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
                "population_policy": "available-valid",
                "complete_selection": "false",
                "selected_planned_runs": 2,
                "available_valid_runs": 2,
                "missing_planned_runs": 0,
                "availability_fraction": 1.0,
                "excluded_attempt_rows": 0,
            }
        ],
    )
    _write_csv(
        analysis_dir / "population_cell_coverage.csv",
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
                "planned_runs": 1,
                "available_valid_runs": 1,
                "missing_runs": 0,
                "availability_fraction": 1.0,
            },
            {
                "subject_id": "subject_b",
                "scenario_id": "scenario_a",
                "planned_runs": 1,
                "available_valid_runs": 1,
                "missing_runs": 0,
                "availability_fraction": 1.0,
            },
        ],
    )
    _write_csv(
        analysis_dir / "population_missing_runs.csv",
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

    _write_csv(
        analysis_dir / "requirement_estimates.csv",
        ["estimand", "subject_id", "requirement_id", "dimension", "p_hat", "coverage"],
        [
            {
                "estimand": "opportunity",
                "subject_id": "subject_a",
                "requirement_id": "requirement_a",
                "dimension": "procedure",
                "p_hat": 1.0,
                "coverage": 1.0,
            }
        ],
    )
    _write_csv(
        analysis_dir / "requirement_intervals.csv",
        [
            "estimand",
            "subject_id",
            "requirement_id",
            "dimension",
            "p_hat",
            "coverage",
            "confidence_level",
            "interval_method",
            "bootstrap_replicates_requested",
            "p_hat_lower",
            "p_hat_upper",
            "p_hat_interval_status",
            "coverage_lower",
            "coverage_upper",
            "coverage_interval_status",
        ],
        [
            {
                "estimand": "opportunity",
                "subject_id": "subject_a",
                "requirement_id": "requirement_a",
                "dimension": "procedure",
                "p_hat": 1.0,
                "coverage": 1.0,
                "confidence_level": 0.95,
                "interval_method": "scenario_run_cluster_percentile_bootstrap",
                "bootstrap_replicates_requested": 2000,
                "p_hat_lower": 1.0,
                "p_hat_upper": 1.0,
                "p_hat_interval_status": "ok",
                "coverage_lower": 1.0,
                "coverage_upper": 1.0,
                "coverage_interval_status": "ok",
            }
        ],
    )
    _write_csv(
        analysis_dir / "dimension_scores.csv",
        ["estimand", "subject_id", "dimension", "aggregation_method", "s_d"],
        [
            {
                "estimand": "opportunity",
                "subject_id": "subject_a",
                "dimension": "procedure",
                "aggregation_method": "arithmetic",
                "s_d": 1.0,
            },
            {
                "estimand": "opportunity",
                "subject_id": "subject_b",
                "dimension": "procedure",
                "aggregation_method": "arithmetic",
                "s_d": 0.5,
            },
        ],
    )
    _write_csv(
        analysis_dir / "dimension_intervals.csv",
        [
            "estimand",
            "subject_id",
            "dimension",
            "aggregation_method",
            "s_d",
            "confidence_level",
            "interval_method",
            "bootstrap_replicates_requested",
            "s_d_lower",
            "s_d_upper",
            "s_d_interval_status",
        ],
        [
            {
                "estimand": "opportunity",
                "subject_id": "subject_a",
                "dimension": "procedure",
                "aggregation_method": "arithmetic",
                "s_d": 1.0,
                "confidence_level": 0.95,
                "interval_method": "scenario_run_cluster_percentile_bootstrap",
                "bootstrap_replicates_requested": 2000,
                "s_d_lower": 1.0,
                "s_d_upper": 1.0,
                "s_d_interval_status": "ok",
            }
        ],
    )


def test_build_analysis_workbook_creates_expected_sheets(tmp_path: Path) -> None:
    export_dir = tmp_path / "execution_example"
    _write_analysis_fixture(export_dir)
    output_path = export_dir / "analysis_output" / "analysis_report.xlsx"

    result = build_analysis_workbook(export_dir, output_path)

    assert result.output_path == output_path
    assert result.sheet_count == 11
    assert output_path.is_file()
    assert output_path.stat().st_size > 10_000

    with zipfile.ZipFile(output_path) as archive:
        workbook_xml = archive.read("xl/workbook.xml")
        shared_strings = archive.read("xl/sharedStrings.xml").decode("utf-8")

    root = ET.fromstring(workbook_xml)
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    names = [
        item.attrib["name"]
        for item in root.findall("main:sheets/main:sheet", namespace)
    ]

    assert names == [
        "Overview",
        "Population Summary",
        "Cell Coverage",
        "Missing Runs",
        "Requirement Estimates",
        "Requirement Intervals",
        "Dimension Scores",
        "Dimension Intervals",
        "Runs",
        "Judgments",
        "Metadata",
    ]
    assert "ACMM Analysis Report" in shared_strings
    assert "Interpretation Notes" in shared_strings
    with zipfile.ZipFile(output_path) as archive:
        assert not any(name.startswith("xl/charts/") for name in archive.namelist())


def test_missing_analysis_table_is_rejected(tmp_path: Path) -> None:
    export_dir = tmp_path / "execution_example"
    _write_analysis_fixture(export_dir)
    (
        export_dir
        / "analysis_output"
        / "data"
        / "compliance"
        / "dimension_intervals.csv"
    ).unlink()

    with pytest.raises(
        analysis_workbook_error,
        match="Could not read analysis table",
    ):
        build_analysis_workbook(export_dir)

def test_build_analysis_workbook_accepts_explicit_analysis_root(tmp_path: Path) -> None:
    export_dir = tmp_path / "export"
    _write_analysis_fixture(export_dir)
    analysis_root = tmp_path / "analysis_run"
    shutil.copytree(export_dir / "analysis_output", analysis_root)
    shutil.rmtree(export_dir / "analysis_output")
    output_path = analysis_root / "analysis_report.xlsx"

    result = build_analysis_workbook(
        export_dir,
        output_path,
        analysis_root=analysis_root,
    )

    assert result.output_path == output_path
    assert output_path.is_file()



def test_campaign_workbook_overview_uses_campaign_and_merge_identity(
    tmp_path: Path,
) -> None:
    export_dir = tmp_path / "campaign_export"
    _write_analysis_fixture(export_dir)
    manifest_path = export_dir / "export_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "0.3"
    manifest["source"] = {
        "kind": "campaign",
        "campaign_id": "campaign_fixture",
        "merge_id": "merge_fixture",
        "experiment_id": "example_experiment_v1",
        "contract_sha256": "a" * 64,
    }
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
        newline="",
    )
    output_path = export_dir / "analysis_output" / "analysis_report.xlsx"

    build_analysis_workbook(export_dir, output_path)

    with zipfile.ZipFile(output_path) as archive:
        shared_strings = archive.read("xl/sharedStrings.xml").decode("utf-8")
    assert "Campaign" in shared_strings
    assert "campaign_fixture" in shared_strings
    assert "Merge" in shared_strings
    assert "merge_fixture" in shared_strings
