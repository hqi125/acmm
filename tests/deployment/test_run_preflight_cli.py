from pathlib import Path

from acmm.cli import _build_parser
from acmm_infra.deployment import write_experiment_apparatus_state
from experiment_package.support import (
    CONTROLLED_TOOL_USE_RUNTIME_SOURCE,
    install_test_package,
    write_package_context,
)


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
    _, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=CONTROLLED_TOOL_USE_RUNTIME_SOURCE,
    )
    write_experiment_apparatus_state(
        runtime_root / "hermes",
        experiment_id="magic_number_tool_use_v1",
        source_sha256=None,
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
            EXPERIMENT_ID,
            "--run-index",
            "0",
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
    monkeypatch,
    capsys,
) -> None:
    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=CONTROLLED_TOOL_USE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, "hermes_nemotron30b")
    write_experiment_apparatus_state(
        runtime_root / "open-webui",
        experiment_id="magic_number_tool_use_v1",
        source_sha256=None,
        application_scope="experiment",
        resources=[],
    )

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            EXPERIMENT_ID,
            "--run-index",
            "0",
            "--run-index",
            "20",
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

    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=CONTROLLED_TOOL_USE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, "hermes_nemotron30b")

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
            EXPERIMENT_ID,
            "--run-index",
            "0",
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
    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=CONTROLLED_TOOL_USE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, "hermes_nemotron30b")

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
            EXPERIMENT_ID,
            "--run-index",
            "0",
            "--preview",
        ]
    )

    assert args.handler(args) == 0
    captured = capsys.readouterr()
    assert "    context:           ready" in captured.out
    assert "Live readiness preflight" not in captured.out


def test_run_start_if_needed_lazily_switches_and_releases_owned_runtimes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from acmm.live_preflight import (
        live_readiness_preflight_result,
        subject_live_readiness,
    )
    from acmm.runtime.planner import planned_run
    from acmm_infra.common import doctor_check

    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=CONTROLLED_TOOL_USE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, "hermes_nemotron30b")
    write_package_context(package, runtime_root, "open_webui_nemotron30b")

    events: list[str] = []

    class fake_lease:
        def __init__(self, subject_id: str) -> None:
            self.subject_id = subject_id
            self.initially_running = False
            self.running = False
            self.started_by_acmm = False

        @property
        def is_running(self) -> bool:
            return self.running

        def ensure_started(self, *, timeout_seconds: float = 120.0) -> bool:
            assert timeout_seconds == 30.0
            events.append(f"start:{self.subject_id}")
            self.running = True
            self.started_by_acmm = True
            return True

        def release(self) -> bool:
            events.append(f"stop:{self.subject_id}")
            self.running = False
            self.started_by_acmm = False
            return True

    leases = {
        "hermes_nemotron30b": fake_lease("hermes_nemotron30b"),
        "open_webui_nemotron30b": fake_lease("open_webui_nemotron30b"),
    }
    monkeypatch.setattr(
        "acmm.cli._discover_runtime_leases",
        lambda **kwargs: leases,
    )

    def ready_live_preflight(**kwargs):
        return live_readiness_preflight_result(
            subjects=tuple(
                subject_live_readiness(
                    subject_id=subject_id,
                    infrastructure_subject=binding.infrastructure_subject,
                    experiment_id=EXPERIMENT_ID,
                    checks=(doctor_check("ready", True, "ready"),),
                    error=None,
                )
                for subject_id, binding in kwargs["subject_bindings"].items()
            )
        )

    monkeypatch.setattr("acmm.cli.inspect_live_readiness", ready_live_preflight)

    def fake_execute(**kwargs):
        before_run = kwargs["before_run"]
        assert before_run is not None
        before_run(
            planned_run(
                run_index=0,
                subject_id="hermes_nemotron30b",
                scenario_id="single_addition_success",
                repetition=1,
            )
        )
        before_run(
            planned_run(
                run_index=20,
                subject_id="open_webui_nemotron30b",
                scenario_id="single_addition_success",
                repetition=1,
            )
        )
        raise ValueError("synthetic stop after lifecycle exercise")

    monkeypatch.setattr("acmm.cli.execute_experiment", fake_execute)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            EXPERIMENT_ID,
            "--run-index",
            "0",
            "--run-index",
            "20",
            "--start-if-needed",
            "--startup-timeout-seconds",
            "30",
            "--results-root",
            str(tmp_path / "results"),
        ]
    )

    assert args.handler(args) == 2
    assert events == [
        "start:hermes_nemotron30b",
        "stop:hermes_nemotron30b",
        "start:open_webui_nemotron30b",
        "stop:open_webui_nemotron30b",
    ]

    output = capsys.readouterr().out
    assert output.index("Live readiness preflight:") < output.index(
        "Experiment execution:"
    )
    assert output.index("Experiment execution:") < output.index(
        "Runtime acquisition:"
    )


def test_preview_start_if_needed_remains_non_contacting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=CONTROLLED_TOOL_USE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, "hermes_nemotron30b")
    write_package_context(package, runtime_root, "open_webui_nemotron30b")

    monkeypatch.setattr(
        "acmm.cli._discover_runtime_leases",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("preview must not inspect live runtime leases")
        ),
    )

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            EXPERIMENT_ID,
            "--run-index",
            "0",
            "--preview",
            "--start-if-needed",
            "--capture-runtime-logs",
        ]
    )

    assert args.handler(args) == 0
