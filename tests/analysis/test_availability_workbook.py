from __future__ import annotations

import csv
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import yaml

from acmm.analysis.availability_workbook import build_availability_workbook


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_fixture(export_dir: Path) -> None:
    analysis_dir = export_dir / "analysis_output" / "data" / "availability"
    export_dir.mkdir(parents=True)
    manifest = {
        "schema_version": "0.1",
        "source": {
            "execution_id": "11111111-1111-4111-8111-111111111111",
            "experiment_id": "example_experiment_v1",
            "contract_sha256": "a" * 64,
        },
    }
    (export_dir / "export_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )

    _write_csv(
        analysis_dir / "availability_summary.csv",
        [
            "scope",
            "subject_id",
            "planned_slots",
            "available_valid_slots",
            "unavailable_slots",
            "availability_fraction",
            "first_attempt_valid_slots",
            "first_attempt_availability_fraction",
            "retried_slots",
            "recovered_by_retry_slots",
            "retry_recovery_fraction",
            "total_attempts",
            "invalid_attempts",
            "mean_attempts_per_slot",
        ],
        [
            {
                "scope": "overall",
                "subject_id": "",
                "planned_slots": 4,
                "available_valid_slots": 3,
                "unavailable_slots": 1,
                "availability_fraction": 0.75,
                "first_attempt_valid_slots": 3,
                "first_attempt_availability_fraction": 0.75,
                "retried_slots": 0,
                "recovered_by_retry_slots": 0,
                "retry_recovery_fraction": "",
                "total_attempts": 4,
                "invalid_attempts": 1,
                "mean_attempts_per_slot": 1.0,
            },
            {
                "scope": "subject",
                "subject_id": "subject_a",
                "planned_slots": 4,
                "available_valid_slots": 3,
                "unavailable_slots": 1,
                "availability_fraction": 0.75,
                "first_attempt_valid_slots": 3,
                "first_attempt_availability_fraction": 0.75,
                "retried_slots": 0,
                "recovered_by_retry_slots": 0,
                "retry_recovery_fraction": "",
                "total_attempts": 4,
                "invalid_attempts": 1,
                "mean_attempts_per_slot": 1.0,
            },
        ],
    )
    _write_csv(
        analysis_dir / "availability_cells.csv",
        [
            "subject_id",
            "scenario_id",
            "planned_slots",
            "available_valid_slots",
            "unavailable_slots",
            "availability_fraction",
            "first_attempt_valid_slots",
            "first_attempt_availability_fraction",
            "retried_slots",
            "recovered_by_retry_slots",
        ],
        [
            {
                "subject_id": "subject_a",
                "scenario_id": "scenario_a",
                "planned_slots": 4,
                "available_valid_slots": 3,
                "unavailable_slots": 1,
                "availability_fraction": 0.75,
                "first_attempt_valid_slots": 3,
                "first_attempt_availability_fraction": 0.75,
                "retried_slots": 0,
                "recovered_by_retry_slots": 0,
            }
        ],
    )
    _write_csv(
        analysis_dir / "availability_positions.csv",
        [
            "subject_id",
            "selected_position_within_repetition",
            "planned_slots",
            "available_valid_slots",
            "unavailable_slots",
            "availability_fraction",
            "first_attempt_valid_slots",
            "first_attempt_availability_fraction",
            "retried_slots",
            "recovered_by_retry_slots",
        ],
        [
            {
                "subject_id": "subject_a",
                "selected_position_within_repetition": 1,
                "planned_slots": 1,
                "available_valid_slots": 1,
                "unavailable_slots": 0,
                "availability_fraction": 1.0,
                "first_attempt_valid_slots": 1,
                "first_attempt_availability_fraction": 1.0,
                "retried_slots": 0,
                "recovered_by_retry_slots": 0,
            }
        ],
    )
    _write_csv(
        analysis_dir / "availability_slots.csv",
        [
            "run_index",
            "subject_id",
            "scenario_id",
            "repetition",
            "selected_position_within_repetition",
            "attempts_used",
            "max_attempts_per_run",
            "first_attempt_valid",
            "retry_attempted",
            "recovered_by_retry",
            "available_valid",
            "valid_attempt",
            "final_termination",
            "final_validity",
            "failure_kind",
            "failure_message",
        ],
        [
            {
                "run_index": 3,
                "subject_id": "subject_a",
                "scenario_id": "scenario_a",
                "repetition": 1,
                "selected_position_within_repetition": 4,
                "attempts_used": 1,
                "max_attempts_per_run": 1,
                "first_attempt_valid": "false",
                "retry_attempted": "false",
                "recovered_by_retry": "false",
                "available_valid": "false",
                "valid_attempt": "",
                "final_termination": "harness_error",
                "final_validity": "invalid",
                "failure_kind": "adapter_process_error",
                "failure_message": "fixture failure",
            }
        ],
    )
    _write_csv(
        analysis_dir / "availability_failure_modes.csv",
        [
            "scope",
            "subject_id",
            "final_termination",
            "failure_kind",
            "unavailable_slots",
            "fraction_of_scope_unavailable",
            "fraction_of_scope_planned",
        ],
        [
            {
                "scope": "subject",
                "subject_id": "subject_a",
                "final_termination": "harness_error",
                "failure_kind": "adapter_process_error",
                "unavailable_slots": 1,
                "fraction_of_scope_unavailable": 1.0,
                "fraction_of_scope_planned": 0.25,
            }
        ],
    )
    _write_csv(
        analysis_dir / "availability_attempt_outcomes.csv",
        [
            "subject_id",
            "scenario_id",
            "attempt",
            "validity",
            "termination",
            "failure_kind",
            "attempt_rows",
        ],
        [
            {
                "subject_id": "subject_a",
                "scenario_id": "scenario_a",
                "attempt": 1,
                "validity": "invalid",
                "termination": "harness_error",
                "failure_kind": "adapter_process_error",
                "attempt_rows": 1,
            }
        ],
    )
    _write_csv(
        analysis_dir / "availability_intervals.csv",
        [
            "subject_id",
            "planned_slots",
            "scenario_clusters",
            "availability_fraction",
            "confidence_level",
            "interval_method",
            "bootstrap_replicates_requested",
            "bootstrap_replicates_finite",
            "availability_lower",
            "availability_upper",
            "interval_status",
        ],
        [
            {
                "subject_id": "subject_a",
                "planned_slots": 4,
                "scenario_clusters": 2,
                "availability_fraction": 0.75,
                "confidence_level": 0.95,
                "interval_method": "scenario_slot_cluster_percentile_bootstrap",
                "bootstrap_replicates_requested": 2000,
                "bootstrap_replicates_finite": 2000,
                "availability_lower": 0.5,
                "availability_upper": 1.0,
                "interval_status": "ok",
            }
        ],
    )
    _write_csv(
        analysis_dir / "availability_sensitivity.csv",
        [
            "estimand",
            "subject_id",
            "requirement_id",
            "dimension",
            "observed_compliant",
            "observed_violations",
            "observed_unadjudicable",
            "observed_not_applicable",
            "observed_adjudicable",
            "observed_p_hat",
            "missing_planned_opportunities",
            "all_missing_violation_lower",
            "all_missing_compliant_upper",
            "sensitivity_width",
            "sensitivity_method",
            "sensitivity_status",
        ],
        [
            {
                "estimand": "opportunity",
                "subject_id": "subject_a",
                "requirement_id": "requirement_a",
                "dimension": "procedure",
                "observed_compliant": 2,
                "observed_violations": 1,
                "observed_unadjudicable": 0,
                "observed_not_applicable": 0,
                "observed_adjudicable": 3,
                "observed_p_hat": 2 / 3,
                "missing_planned_opportunities": 1,
                "all_missing_violation_lower": 0.5,
                "all_missing_compliant_upper": 0.75,
                "sensitivity_width": 0.25,
                "sensitivity_method": (
                    "all_missing_violations_to_all_missing_compliant_"
                    "planned_opportunity_envelope"
                ),
                "sensitivity_status": "ok",
            }
        ],
    )


def test_build_availability_workbook_creates_separate_report(tmp_path: Path) -> None:
    export_dir = tmp_path / "execution_example"
    _write_fixture(export_dir)
    output_path = export_dir / "analysis_output" / "availability_analysis.xlsx"

    result = build_availability_workbook(export_dir, output_path)

    assert result.output_path == output_path
    assert result.sheet_count == 9
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
        "Summary",
        "Cells",
        "Positions",
        "Slot Outcomes",
        "Failure Modes",
        "Attempt Outcomes",
        "Intervals",
        "Sensitivity",
    ]
    assert "ACMM Availability Analysis" in shared_strings
    assert "Sensitivity envelope" in shared_strings
    assert "fixture failure" in shared_strings
