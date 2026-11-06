from pathlib import Path

from acmm.cli import _build_parser
from experiment_package.support import (
    PACKAGE_SMOKE_CONTRACT,
    SMOKE_RUNTIME_SOURCE,
    install_test_package,
    write_package_context,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = PACKAGE_SMOKE_CONTRACT
EXPERIMENT_ID = "package_smoke_v1"


def test_real_run_rejects_wrong_deployment_context_before_execution(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, "hermes_gpt_oss120b")
    marker = runtime_root / "hermes" / ".acmm-experiment-package.json"
    payload = __import__("json").loads(marker.read_text(encoding="utf-8"))
    payload["experiment_id"] = "other_package_v1"
    marker.write_text(__import__("json").dumps(payload), encoding="utf-8")

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
    assert "active experiment is 'other_package_v1'" in captured.err
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
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, "hermes_gpt_oss120b")
    write_package_context(package, runtime_root, "open_webui_gpt_oss120b")
    marker = runtime_root / "open-webui" / ".acmm-experiment-package.json"
    payload = __import__("json").loads(marker.read_text(encoding="utf-8"))
    payload["experiment_id"] = "other_package_v1"
    marker.write_text(__import__("json").dumps(payload), encoding="utf-8")

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            EXPERIMENT_ID,
            "--run-index",
            "0",
            "--run-index",
            "2",
            "--results-root",
            str(tmp_path / "results"),
        ]
    )

    assert args.handler(args) == 2
    assert not (tmp_path / "results").exists()

    captured = capsys.readouterr()
    assert "  hermes_gpt_oss120b:\n    context:         ready" in captured.out
    assert "  open_webui_gpt_oss120b:\n    context:         not-ready" in captured.out
    assert "active experiment is 'other_package_v1'" in captured.err
    assert "no execution session was created" in captured.err


def test_real_run_rejects_live_not_ready_before_adapter_construction(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from dataclasses import replace

    from acmm.preflight.live import (
        live_readiness_preflight_result,
        subject_live_readiness,
    )
    from acmm_infra.common import doctor_check

    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, "hermes_gpt_oss120b")

    binding = _binding_with_builder(
        "hermes_gpt_oss120b",
        lambda: (_ for _ in ()).throw(
            AssertionError("adapter construction must not occur after live failure")
        ),
    )
    monkeypatch.setattr(
        "acmm.cli.lookup_subject_execution_binding",
        lambda subject_id: binding if subject_id == "hermes_gpt_oss120b" else None,
    )
    monkeypatch.setattr(
        "acmm.cli.inspect_live_readiness",
        lambda **kwargs: live_readiness_preflight_result(
            subjects=(
                subject_live_readiness(
                    subject_id="hermes_gpt_oss120b",
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
    assert "  hermes_gpt_oss120b:\n    readiness: not-ready" in captured.out
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
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, "hermes_gpt_oss120b")

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
    assert "    context:         ready" in captured.out
    assert "Live readiness preflight" not in captured.out


def test_run_start_if_needed_lazily_switches_and_releases_owned_runtimes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from acmm.preflight.live import (
        live_readiness_preflight_result,
        subject_live_readiness,
    )
    from acmm.runtime.planner import planned_run
    from acmm_infra.common import doctor_check

    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, "hermes_gpt_oss120b")
    write_package_context(package, runtime_root, "open_webui_gpt_oss120b")

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
        "hermes_gpt_oss120b": fake_lease("hermes_gpt_oss120b"),
        "open_webui_gpt_oss120b": fake_lease("open_webui_gpt_oss120b"),
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
                subject_id="hermes_gpt_oss120b",
                scenario_id="single_addition_success",
                repetition=1,
            )
        )
        before_run(
            planned_run(
                run_index=20,
                subject_id="open_webui_gpt_oss120b",
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
            "2",
            "--start-if-needed",
            "--startup-timeout-seconds",
            "30",
            "--results-root",
            str(tmp_path / "results"),
        ]
    )

    assert args.handler(args) == 2
    assert events == [
        "start:hermes_gpt_oss120b",
        "stop:hermes_gpt_oss120b",
        "start:open_webui_gpt_oss120b",
        "stop:open_webui_gpt_oss120b",
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
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, "hermes_gpt_oss120b")
    write_package_context(package, runtime_root, "open_webui_gpt_oss120b")

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


def test_runtime_acquisition_retries_with_progressive_cooldown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from acmm.cli import _ensure_runtime_ready
    from acmm.preflight.live import (
        live_readiness_preflight_result,
        subject_live_readiness,
    )
    from acmm.subjects.catalog import lookup_subject_execution_binding
    from acmm_infra.common import doctor_check, infrastructure_error

    binding = lookup_subject_execution_binding("agent_zero_gpt_oss120b")
    assert binding is not None

    events: list[str] = []
    delays: list[float] = []

    class fake_lease:
        initially_running = False
        started_by_acmm = False
        running = False
        attempts = 0

        @property
        def is_running(self) -> bool:
            return self.running

        def ensure_started(self, *, timeout_seconds: float = 120.0) -> bool:
            assert timeout_seconds == 30.0
            self.attempts += 1
            events.append(f"start:{self.attempts}")
            if self.attempts < 3:
                raise infrastructure_error("synthetic startup failure")
            self.running = True
            self.started_by_acmm = True
            return True

        def release(self) -> bool:
            events.append("stop")
            self.running = False
            self.started_by_acmm = False
            return True

    lease = fake_lease()

    monkeypatch.setattr(
        "acmm.cli.inspect_live_readiness",
        lambda **kwargs: live_readiness_preflight_result(
            subjects=(
                subject_live_readiness(
                    subject_id="agent_zero_gpt_oss120b",
                    infrastructure_subject="agent-zero",
                    experiment_id=EXPERIMENT_ID,
                    checks=(doctor_check("ready", True, "ready"),),
                    error=None,
                ),
            )
        ),
    )
    monkeypatch.setattr("acmm.cli.time.sleep", delays.append)

    _ensure_runtime_ready(
        subject_id="agent_zero_gpt_oss120b",
        lease=lease,
        binding=binding,
        repo_root=tmp_path,
        runtime_root=tmp_path,
        startup_timeout_seconds=30.0,
        startup_max_attempts=3,
    )

    assert events == ["start:1", "start:2", "start:3"]
    assert delays == [1.0, 5.0]
    assert lease.running is True
    assert lease.started_by_acmm is True


def test_run_can_prestart_all_selected_subjects_before_execution_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from acmm.preflight.live import (
        live_readiness_preflight_result,
        subject_live_readiness,
    )
    from acmm.runtime.planner import planned_run
    from acmm_infra.common import doctor_check

    package, runtime_root = install_test_package(
        tmp_path,
        monkeypatch,
        contract_path=CONTRACT,
        runtime_source=SMOKE_RUNTIME_SOURCE,
    )
    write_package_context(package, runtime_root, "hermes_gpt_oss120b")
    write_package_context(package, runtime_root, "open_webui_gpt_oss120b")

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
        "hermes_gpt_oss120b": fake_lease("hermes_gpt_oss120b"),
        "open_webui_gpt_oss120b": fake_lease("open_webui_gpt_oss120b"),
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
        events.append("execute")
        assert events[:2] == [
            "start:hermes_gpt_oss120b",
            "start:open_webui_gpt_oss120b",
        ]
        before_run = kwargs["before_run"]
        assert before_run is not None
        before_run(
            planned_run(
                run_index=0,
                subject_id="hermes_gpt_oss120b",
                scenario_id="single_addition_success",
                repetition=1,
            )
        )
        before_run(
            planned_run(
                run_index=20,
                subject_id="open_webui_gpt_oss120b",
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
            "2",
            "--start-if-needed",
            "--prestart-selected-subjects",
            "--results-root",
            str(tmp_path / "results"),
        ]
    )

    assert args.handler(args) == 2
    assert events == [
        "start:hermes_gpt_oss120b",
        "start:open_webui_gpt_oss120b",
        "execute",
        "stop:hermes_gpt_oss120b",
        "stop:open_webui_gpt_oss120b",
    ]
