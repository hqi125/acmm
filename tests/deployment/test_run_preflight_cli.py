from pathlib import Path

from acmm.cli import _build_parser
from acmm_infra.deployment import write_experiment_apparatus_state
from acmm_infra.profiles.experiments import experiment_apparatus_sha256


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = (
    ROOT
    / "experiments"
    / "examples"
    / "controlled_tool_use_multiscenario"
    / "experiment.yaml"
)
EXPERIMENT_ID = "controlled_tool_use_multiscenario_v1"


def test_real_run_rejects_wrong_deployment_context_before_execution(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    runtime_root = tmp_path / "runtime"
    write_experiment_apparatus_state(
        runtime_root / "hermes",
        experiment_id="magic_number_tool_use_v1",
        source_sha256=experiment_apparatus_sha256(
            ROOT,
            "hermes",
            "magic_number_tool_use_v1",
        ),
        application_scope="experiment",
        resources=[],
    )

    def forbidden_adapter_builder():
        raise AssertionError("adapter construction must not occur after preflight failure")

    monkeypatch.setattr(
        "acmm.cli.lookup_subject_execution_binding",
        lambda subject_id: _binding_with_builder(subject_id, forbidden_adapter_builder),
    )

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            str(CONTRACT),
            "--run-index",
            "0",
            "--runtime-root",
            str(runtime_root),
            "--results-root",
            str(tmp_path / "results"),
        ]
    )

    assert args.handler(args) == 2
    assert not (tmp_path / "results").exists()
    captured = capsys.readouterr()
    assert "active experiment is 'magic_number_tool_use_v1'" in captured.err
    assert "no execution session was created" in captured.err


def _binding_with_builder(subject_id: str, builder):
    from dataclasses import replace
    from acmm.subjects.catalog import lookup_subject_execution_binding

    binding = lookup_subject_execution_binding(subject_id)
    assert binding is not None
    return replace(binding, adapter_builder=builder)

def test_real_run_requires_selected_subjects_to_converge_on_contract_experiment(
    tmp_path: Path,
    capsys,
) -> None:
    runtime_root = tmp_path / "runtime"

    write_experiment_apparatus_state(
        runtime_root / "hermes",
        experiment_id=EXPERIMENT_ID,
        source_sha256=experiment_apparatus_sha256(
            ROOT,
            "hermes",
            EXPERIMENT_ID,
        ),
        application_scope="experiment",
        resources=[],
    )
    write_experiment_apparatus_state(
        runtime_root / "open-webui",
        experiment_id="magic_number_tool_use_v1",
        source_sha256=experiment_apparatus_sha256(
            ROOT,
            "open-webui",
            "magic_number_tool_use_v1",
        ),
        application_scope="experiment",
        resources=[],
    )

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            str(CONTRACT),
            "--run-index",
            "0",
            "--run-index",
            "20",
            "--runtime-root",
            str(runtime_root),
            "--results-root",
            str(tmp_path / "results"),
        ]
    )

    assert args.handler(args) == 2
    assert not (tmp_path / "results").exists()

    captured = capsys.readouterr()
    assert "  hermes_nemotron30b:\n    context:           ready" in captured.out
    assert "  open_webui_nemotron30b:\n    context:           not-ready" in captured.out
    assert "active experiment is 'magic_number_tool_use_v1'" in captured.err
    assert "no execution session was created" in captured.err


def test_real_run_rejects_live_not_ready_before_adapter_construction(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from dataclasses import replace

    from acmm.live_preflight import (
        live_readiness_preflight_result,
        subject_live_readiness,
    )
    from acmm_infra.common import doctor_check

    runtime_root = tmp_path / "runtime"
    write_experiment_apparatus_state(
        runtime_root / "hermes",
        experiment_id=EXPERIMENT_ID,
        source_sha256=experiment_apparatus_sha256(
            ROOT,
            "hermes",
            EXPERIMENT_ID,
        ),
        application_scope="experiment",
        resources=[],
    )

    binding = _binding_with_builder(
        "hermes_nemotron30b",
        lambda: (_ for _ in ()).throw(
            AssertionError("adapter construction must not occur after live failure")
        ),
    )
    monkeypatch.setattr(
        "acmm.cli.lookup_subject_execution_binding",
        lambda subject_id: binding if subject_id == "hermes_nemotron30b" else None,
    )
    monkeypatch.setattr(
        "acmm.cli.inspect_live_readiness",
        lambda **kwargs: live_readiness_preflight_result(
            subjects=(
                subject_live_readiness(
                    subject_id="hermes_nemotron30b",
                    infrastructure_subject="hermes",
                    experiment_id=EXPERIMENT_ID,
                    checks=(
                        doctor_check(
                            "container_running",
                            False,
                            "Hermes container is not running.",
                        ),
                    ),
                    error=None,
                ),
            )
        ),
    )

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            str(CONTRACT),
            "--run-index",
            "0",
            "--runtime-root",
            str(runtime_root),
            "--results-root",
            str(tmp_path / "results"),
        ]
    )

    assert args.handler(args) == 2
    assert not (tmp_path / "results").exists()

    captured = capsys.readouterr()
    assert "Live readiness preflight:" in captured.out
    assert "  hermes_nemotron30b:\n    readiness: not-ready" in captured.out
    assert "[container_running]" in captured.err
    assert "no execution session was created" in captured.err


def test_preview_does_not_run_live_readiness(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    runtime_root = tmp_path / "runtime"
    write_experiment_apparatus_state(
        runtime_root / "hermes",
        experiment_id=EXPERIMENT_ID,
        source_sha256=experiment_apparatus_sha256(
            ROOT,
            "hermes",
            EXPERIMENT_ID,
        ),
        application_scope="experiment",
        resources=[],
    )

    def forbidden_live_readiness(**kwargs):
        del kwargs
        raise AssertionError("preview must not perform live readiness checks")

    monkeypatch.setattr(
        "acmm.cli.inspect_live_readiness",
        forbidden_live_readiness,
    )

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            str(CONTRACT),
            "--run-index",
            "0",
            "--preview",
            "--runtime-root",
            str(runtime_root),
        ]
    )

    assert args.handler(args) == 0
    captured = capsys.readouterr()
    assert "    context:           ready" in captured.out
    assert "Live readiness preflight" not in captured.out
