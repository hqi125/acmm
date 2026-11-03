from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOL_PATH = _REPO_ROOT / "tools" / "cold_start_qualification.py"
_TOOL_SPEC = importlib.util.spec_from_file_location(
    "acmm_repo_cold_start_qualification",
    _TOOL_PATH,
)

assert _TOOL_SPEC is not None
assert _TOOL_SPEC.loader is not None

cold_start = importlib.util.module_from_spec(_TOOL_SPEC)
sys.modules[_TOOL_SPEC.name] = cold_start
_TOOL_SPEC.loader.exec_module(cold_start)


def test_run_command_preserves_selection_and_diagnostics(tmp_path: Path) -> None:
    command = cold_start._build_run_command(
        acmm_command="acmm",
        experiment="package_smoke_v1",
        subject_id="agent_zero_nemotron30b",
        run_indices=("120-123", "128"),
        results_root=tmp_path / "results",
        startup_timeout_seconds=90.0,
        capture_runtime_logs=True,
    )

    assert command[:4] == [
        "acmm",
        "run",
        "package_smoke_v1",
        "--subject",
    ]
    assert command.count("--run-index") == 2
    assert command[command.index("--run-index") + 1] == "120-123"
    second = command.index("--run-index", command.index("--run-index") + 1)
    assert command[second + 1] == "128"
    assert "--start-if-needed" in command
    assert "--capture-runtime-logs" in command


def test_subject_running_requires_selected_subject() -> None:
    status = {
        "subjects": [
            {"subject_id": "agent_zero_nemotron30b", "running": False},
        ]
    }

    assert not cold_start._subject_running(status, "agent_zero_nemotron30b")

    with pytest.raises(
        cold_start.cold_start_qualification_error,
        match="did not include subject",
    ):
        cold_start._subject_running(status, "hermes_nemotron30b")


def test_extract_session_reads_compact_execution_summary() -> None:
    output = """
Experiment execution persisted successfully.
  session:      results/example/execution_1234
  selected:     4 of 180
"""

    assert cold_start._extract_session(output) == "results/example/execution_1234"
    assert cold_start._extract_session("no execution created\n") is None


def test_preview_command_is_passive_and_preserves_selection(tmp_path: Path) -> None:
    command = cold_start._build_preview_command(
        acmm_command="acmm",
        experiment="package_smoke_v1",
        subject_id="agent_zero_nemotron30b",
        run_indices=("120-123",),
    )

    assert "--preview" in command
    assert "--start-if-needed" not in command
    assert "--capture-runtime-logs" not in command
    assert command[command.index("--run-index") + 1] == "120-123"


def test_status_command_is_comprehensive_and_has_no_subject_selector(tmp_path: Path) -> None:
    command = cold_start._build_status_command(
        "acmm",
        tmp_path,
        tmp_path / "runtime",
    )

    assert command[:3] == ["acmm", "env", "status"]
    assert "--subject" not in command
    assert "--json" in command
