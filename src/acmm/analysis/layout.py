"""Filesystem layout helpers for ACMM analysis artifacts."""
from __future__ import annotations

from pathlib import Path


ANALYSIS_OUTPUT_DIR_NAME = "analysis_output"
DATA_DIR_NAME = "data"
LOGS_DIR_NAME = "logs"
FIGURES_DIR_NAME = "figures"

COMPLIANCE_SCOPE = "compliance"
AVAILABILITY_SCOPE = "availability"
INTERACTION_SCOPE = "interaction"


def analysis_output_dir(export_dir: str | Path) -> Path:
    return Path(export_dir) / ANALYSIS_OUTPUT_DIR_NAME


def analysis_data_dir(export_dir: str | Path, scope: str) -> Path:
    return analysis_output_dir(export_dir) / DATA_DIR_NAME / scope


def analysis_log_dir(export_dir: str | Path, scope: str) -> Path:
    return analysis_output_dir(export_dir) / LOGS_DIR_NAME / scope


def analysis_figure_dir(export_dir: str | Path, scope: str) -> Path:
    return analysis_output_dir(export_dir) / FIGURES_DIR_NAME / scope
