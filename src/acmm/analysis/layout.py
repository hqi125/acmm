from __future__ import annotations

from pathlib import Path


ANALYSIS_OUTPUT_DIR_NAME = "analysis_output"
DATA_DIR_NAME = "data"
LOGS_DIR_NAME = "logs"
FIGURE_DIR_NAME = "figures"

COMPLIANCE_SCOPE = "compliance"
AVAILABILITY_SCOPE = "availability"
INTERACTION_SCOPE = "interaction"


def analysis_output_dir(export_dir: str | Path) -> Path:
    return Path(export_dir) / ANALYSIS_OUTPUT_DIR_NAME


def analysis_data_dir_from_root(analysis_root: str | Path, scope: str) -> Path:
    return Path(analysis_root) / DATA_DIR_NAME / scope


def analysis_log_dir_from_root(analysis_root: str | Path, scope: str) -> Path:
    return Path(analysis_root) / LOGS_DIR_NAME / scope


def analysis_figure_dir_from_root(analysis_root: str | Path, scope: str) -> Path:
    return Path(analysis_root) / FIGURE_DIR_NAME / scope


def analysis_data_dir(export_dir: str | Path, scope: str) -> Path:
    return analysis_data_dir_from_root(analysis_output_dir(export_dir), scope)


def analysis_log_dir(export_dir: str | Path, scope: str) -> Path:
    return analysis_log_dir_from_root(analysis_output_dir(export_dir), scope)


def analysis_figure_dir(export_dir: str | Path, scope: str) -> Path:
    return analysis_figure_dir_from_root(analysis_output_dir(export_dir), scope)
