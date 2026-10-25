from pathlib import Path

import pytest

import acmm_infra.cohort as cohort_module
from acmm_infra.cohort import (
    apply_cohort,
    cohort_subject,
    cohort_subject_status,
    experiment_cohort,
    resolve_experiment_cohort,
    summarize_context,
)
from acmm_infra.common import infrastructure_error
from acmm_infra.subject_managers import subject_manager_context


ROOT = Path(__file__).resolve().parents[2]


class _fake_docker:
    def __init__(self, running: bool) -> None:
        self._running = running

    def container_running(self) -> bool:
        return self._running


class _fake_manager:
    def __init__(self, running: bool = False) -> None:
        self.docker = _fake_docker(running)
        self.applied = False

    def apply(self, *, timeout_seconds: float = 120.0, progress=None):
        del timeout_seconds
        self.applied = True
        if progress is not None:
            progress("fake apply")
        return {}


def test_resolve_experiment_cohort_uses_canonical_contract_subjects() -> None:
    cohort = resolve_experiment_cohort(
        ROOT,
        "controlled_tool_use_multiscenario_v1",
    )

    assert cohort.experiment_id == "controlled_tool_use_multiscenario_v1"
    assert cohort.canonical_contract == (
        ROOT
        / "experiments/examples/controlled_tool_use_multiscenario/experiment.yaml"
    ).resolve()
    assert [item.subject_id for item in cohort.subjects] == [
        "hermes_nemotron30b",
        "open_webui_nemotron30b",
        "agent_zero_nemotron30b",
    ]
    assert [item.infrastructure_subject for item in cohort.subjects] == [
        "hermes",
        "open-webui",
        "agent-zero",
    ]


def test_resolve_experiment_cohort_can_select_contract_subjects() -> None:
    cohort = resolve_experiment_cohort(
        ROOT,
        "controlled_tool_use_multiscenario_v1",
        ("agent_zero_nemotron30b", "hermes_nemotron30b"),
    )

    assert [item.subject_id for item in cohort.subjects] == [
        "hermes_nemotron30b",
        "agent_zero_nemotron30b",
    ]


def test_resolve_experiment_cohort_rejects_subject_outside_contract() -> None:
    with pytest.raises(infrastructure_error, match="not covered by this cohort"):
        resolve_experiment_cohort(
            ROOT,
            "controlled_tool_use_multiscenario_v1",
            ("not_a_subject",),
        )


def test_apply_cohort_checks_all_runtimes_before_mutating(monkeypatch, tmp_path: Path) -> None:
    managers = {
        "hermes": _fake_manager(running=False),
        "open-webui": _fake_manager(running=True),
    }

    def fake_builder(repo_root, runtime_root, infrastructure_subject, experiment_id):
        del repo_root, runtime_root
        return subject_manager_context(
            infrastructure_subject=infrastructure_subject,
            experiment_id=experiment_id,
            manager=managers[infrastructure_subject],
        )

    monkeypatch.setattr(cohort_module, "build_subject_manager", fake_builder)
    cohort = experiment_cohort(
        experiment_id="example_v1",
        canonical_contract=tmp_path / "experiment.yaml",
        subjects=(
            cohort_subject("subject_a", "hermes"),
            cohort_subject("subject_b", "open-webui"),
        ),
    )

    with pytest.raises(infrastructure_error, match="Still running: subject_b"):
        apply_cohort(
            tmp_path,
            tmp_path / "runtime",
            cohort,
            timeout_seconds=120.0,
            progress=lambda *args: None,
        )

    assert managers["hermes"].applied is False
    assert managers["open-webui"].applied is False


def test_summarize_context_distinguishes_convergence_states() -> None:
    def status(subject_id: str, experiment_id: str | None):
        return cohort_subject_status(
            subject_id=subject_id,
            infrastructure_subject=subject_id,
            running=False,
            experiment_id=experiment_id,
            application_scope="experiment" if experiment_id else None,
            apparatus_state="match" if experiment_id else "none",
            source_sha256=None,
        )

    assert summarize_context((status("a", None), status("b", None))).state == "baseline"
    converged = summarize_context((status("a", "x"), status("b", "x")))
    assert converged.state == "converged"
    assert converged.experiment_id == "x"
    assert summarize_context((status("a", "x"), status("b", None))).state == "partial"
    assert summarize_context((status("a", "x"), status("b", "y"))).state == "divergent"


def test_apply_cohort_reports_partial_failure_without_rollback(monkeypatch, tmp_path: Path) -> None:
    first = _fake_manager(running=False)

    class failing_manager(_fake_manager):
        def apply(self, *, timeout_seconds: float = 120.0, progress=None):
            del timeout_seconds, progress
            raise infrastructure_error("synthetic failure")

    second = failing_manager(running=False)
    managers = {"hermes": first, "open-webui": second}

    def fake_builder(repo_root, runtime_root, infrastructure_subject, experiment_id):
        del repo_root, runtime_root
        return subject_manager_context(
            infrastructure_subject=infrastructure_subject,
            experiment_id=experiment_id,
            manager=managers[infrastructure_subject],
        )

    def fake_inspect(repo_root, runtime_root, subject, include_running=True):
        del repo_root, runtime_root, include_running
        return cohort_subject_status(
            subject_id=subject.subject_id,
            infrastructure_subject=subject.infrastructure_subject,
            running=None,
            experiment_id="example_v1",
            application_scope="experiment",
            apparatus_state="match",
            source_sha256="abc",
        )

    monkeypatch.setattr(cohort_module, "build_subject_manager", fake_builder)
    monkeypatch.setattr(cohort_module, "inspect_subject_context", fake_inspect)
    cohort = experiment_cohort(
        experiment_id="example_v1",
        canonical_contract=tmp_path / "experiment.yaml",
        subjects=(
            cohort_subject("subject_a", "hermes"),
            cohort_subject("subject_b", "open-webui"),
        ),
    )

    with pytest.raises(
        infrastructure_error,
        match=r"after 1/2 subject\(s\) were applied.*no automatic rollback",
    ):
        apply_cohort(
            tmp_path,
            tmp_path / "runtime",
            cohort,
            timeout_seconds=120.0,
            progress=lambda *args: None,
        )

    assert first.applied is True


def test_doctor_cohort_temporarily_starts_subjects_sequentially(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from acmm_infra.common import doctor_check, doctor_report
    from acmm_infra.readiness import deployed_subject_doctor_result

    events: list[str] = []

    class fake_lease:
        def __init__(self, subject: str) -> None:
            self.subject = subject
            self.initially_running = False
            self.started_by_acmm = False

        def ensure_started(self, *, timeout_seconds: float = 120.0) -> bool:
            assert timeout_seconds == 45.0
            events.append(f"start:{self.subject}")
            self.started_by_acmm = True
            return True

        def release(self) -> bool:
            events.append(f"stop:{self.subject}")
            self.started_by_acmm = False
            return True

    monkeypatch.setattr(
        cohort_module,
        "discover_runtime_lease",
        lambda repo_root, runtime_root, infrastructure_subject: fake_lease(
            infrastructure_subject
        ),
    )

    def fake_doctor(repo_root, runtime_root, infrastructure_subject):
        del repo_root, runtime_root
        events.append(f"doctor:{infrastructure_subject}")
        return deployed_subject_doctor_result(
            infrastructure_subject=infrastructure_subject,
            experiment_id="example_v1",
            report=doctor_report(
                checks=(doctor_check("ready", True, "ready"),)
            ),
            error=None,
        )

    monkeypatch.setattr(cohort_module, "doctor_deployed_subject", fake_doctor)
    monkeypatch.setattr(
        cohort_module,
        "deployed_experiment_state",
        lambda *args, **kwargs: {
            "application_scope": "experiment",
            "source_sha256": "abc",
        },
    )

    subjects = (
        cohort_subject("subject_a", "hermes"),
        cohort_subject("subject_b", "open-webui"),
    )
    results = cohort_module.doctor_cohort(
        tmp_path,
        tmp_path / "runtime",
        subjects,
        start_if_needed=True,
        startup_timeout_seconds=45.0,
    )

    assert all(item.is_ready for item in results)
    assert all(item.started_by_acmm for item in results)
    assert events == [
        "start:hermes",
        "doctor:hermes",
        "stop:hermes",
        "start:open-webui",
        "doctor:open-webui",
        "stop:open-webui",
    ]


def test_stop_cohort_ignores_experiment_convergence(monkeypatch, tmp_path: Path) -> None:
    class fake_manager:
        def __init__(self) -> None:
            self.down_calls = 0

        def down(self) -> None:
            self.down_calls += 1

    class fake_lease:
        def __init__(self, running: bool) -> None:
            self.initially_running = running
            self.context = type("context", (), {"manager": fake_manager()})()

    leases = {
        "hermes": fake_lease(True),
        "open-webui": fake_lease(False),
    }
    monkeypatch.setattr(
        cohort_module,
        "discover_runtime_lease",
        lambda repo_root, runtime_root, infrastructure_subject: leases[
            infrastructure_subject
        ],
    )

    results = cohort_module.stop_cohort(
        tmp_path,
        tmp_path / "runtime",
        (
            cohort_subject("subject_a", "hermes"),
            cohort_subject("subject_b", "open-webui"),
        ),
    )

    assert results[0].stopped is True
    assert results[1].stopped is False
    assert leases["hermes"].context.manager.down_calls == 1
    assert leases["open-webui"].context.manager.down_calls == 0
