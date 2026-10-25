from __future__ import annotations

from pathlib import Path

from acmm.cli import _build_parser
from acmm.subjects.catalog import lookup_subject_execution_binding
from acmm_infra.deployment import write_experiment_apparatus_state
from acmm_infra.profiles.experiments import experiment_apparatus_sha256


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_1 = "controlled_tool_use_multiscenario_v1"
EXPERIMENT_2 = "controlled_scoped_record_update_v1"
PILOT = "magic_number_tool_use_v1"
SUBJECTS = (
    "hermes_nemotron30b",
    "open_webui_nemotron30b",
    "agent_zero_nemotron30b",
)


def _write_context(
    runtime_root: Path,
    subject_id: str,
    experiment_id: str,
    *,
    application_scope: str = "experiment",
) -> None:
    binding = lookup_subject_execution_binding(subject_id)
    assert binding is not None

    write_experiment_apparatus_state(
        runtime_root / binding.infrastructure_subject,
        experiment_id=experiment_id,
        source_sha256=experiment_apparatus_sha256(
            ROOT,
            binding.infrastructure_subject,
            experiment_id,
        ),
        application_scope=application_scope,
        resources=[],
    )


def test_bare_run_preview_infers_converged_experiment(
    tmp_path: Path,
    capsys,
) -> None:
    runtime_root = tmp_path / "runtime"
    for subject_id in SUBJECTS:
        _write_context(runtime_root, subject_id, EXPERIMENT_1)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "--preview",
            "--repo-root",
            str(ROOT),
            "--runtime-root",
            str(runtime_root),
        ]
    )

    assert args.handler(args) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Experiment resolution:" in captured.out
    assert f"  experiment:         {EXPERIMENT_1}" in captured.out
    assert "controlled_tool_use_multiscenario/experiment.yaml" in captured.out
    assert "selected:      60" in captured.out
    assert captured.out.count("    context:           ready") == 3


def test_bare_run_preview_infers_scoped_record_experiment(
    tmp_path: Path,
    capsys,
) -> None:
    runtime_root = tmp_path / "runtime"
    for subject_id in SUBJECTS:
        _write_context(runtime_root, subject_id, EXPERIMENT_2)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "--preview",
            "--repo-root",
            str(ROOT),
            "--runtime-root",
            str(runtime_root),
        ]
    )

    assert args.handler(args) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert f"  experiment:         {EXPERIMENT_2}" in captured.out
    assert "controlled_scoped_record_update/experiment.yaml" in captured.out
    assert "selected:      60" in captured.out
    assert captured.out.count("    context:           ready") == 3


def test_bare_run_preview_rejects_divergent_experiments(
    tmp_path: Path,
    capsys,
) -> None:
    runtime_root = tmp_path / "runtime"
    _write_context(runtime_root, SUBJECTS[0], EXPERIMENT_1)
    _write_context(runtime_root, SUBJECTS[1], PILOT)
    _write_context(runtime_root, SUBJECTS[2], EXPERIMENT_1)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "--preview",
            "--repo-root",
            str(ROOT),
            "--runtime-root",
            str(runtime_root),
        ]
    )

    assert args.handler(args) == 2

    captured = capsys.readouterr()
    assert "do not converge" in captured.err
    assert EXPERIMENT_1 in captured.err
    assert PILOT in captured.err
    assert "Run selection preview" not in captured.out


def test_bare_run_preview_uses_unique_candidate_with_missing_context(
    tmp_path: Path,
    capsys,
) -> None:
    runtime_root = tmp_path / "runtime"
    _write_context(runtime_root, SUBJECTS[0], EXPERIMENT_1)
    _write_context(runtime_root, SUBJECTS[1], EXPERIMENT_1)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "--preview",
            "--repo-root",
            str(ROOT),
            "--runtime-root",
            str(runtime_root),
        ]
    )

    assert args.handler(args) == 0

    captured = capsys.readouterr()
    assert "Experiment resolution:" in captured.out
    assert f"  experiment:         {EXPERIMENT_1}" in captured.out
    assert "  agent_zero_nemotron30b:" in captured.out
    assert "    context:           not-ready" in captured.out
    assert "no active experiment deployment context" in captured.err
    assert "Run selection preview" in captured.out


def test_bare_run_subject_filter_scopes_context_inference(
    tmp_path: Path,
    capsys,
) -> None:
    runtime_root = tmp_path / "runtime"
    _write_context(runtime_root, SUBJECTS[0], PILOT)
    _write_context(runtime_root, SUBJECTS[1], EXPERIMENT_1)
    _write_context(runtime_root, SUBJECTS[2], EXPERIMENT_1)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "--subject",
            SUBJECTS[0],
            "--preview",
            "--repo-root",
            str(ROOT),
            "--runtime-root",
            str(runtime_root),
        ]
    )

    assert args.handler(args) == 0

    captured = capsys.readouterr()
    assert "Experiment resolution:" in captured.out
    assert f"  experiment:         {PILOT}" in captured.out
    assert "magic_number/experiment.yaml" in captured.out
    assert f"subjects:      {SUBJECTS[0]}" in captured.out
    assert SUBJECTS[1] not in captured.err
    assert SUBJECTS[2] not in captured.err


def test_bare_run_preview_rejects_absent_contexts(
    tmp_path: Path,
    capsys,
) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "--preview",
            "--repo-root",
            str(ROOT),
            "--runtime-root",
            str(tmp_path / "runtime"),
        ]
    )

    assert args.handler(args) == 2

    captured = capsys.readouterr()
    assert "no selected deployment context declares an experiment" in captured.err
    assert "Run selection preview" not in captured.out
