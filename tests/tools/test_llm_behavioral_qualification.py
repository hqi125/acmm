from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from acmm_infra.cohort import cohort_subject, cohort_subject_status, experiment_cohort


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOL_PATH = _REPO_ROOT / "tools" / "qualify_llm_behavior.py"
_TOOL_SPEC = importlib.util.spec_from_file_location(
    "acmm_repo_qualify_llm_behavior",
    _TOOL_PATH,
)
assert _TOOL_SPEC is not None
assert _TOOL_SPEC.loader is not None
qualification = importlib.util.module_from_spec(_TOOL_SPEC)
sys.modules[_TOOL_SPEC.name] = qualification
_TOOL_SPEC.loader.exec_module(qualification)


def _cohort() -> experiment_cohort:
    baseline = "gpt_oss_120b_cloud_candidate_v1"
    return experiment_cohort(
        experiment_id=None,
        canonical_contract=None,
        subjects=(
            cohort_subject("hermes_llm_qualification", "hermes", baseline),
            cohort_subject("open_webui_llm_qualification", "open-webui", baseline),
            cohort_subject("agent_zero_llm_qualification", "agent-zero", baseline),
        ),
    )


def _status(
    subject: cohort_subject,
    *,
    running: bool = False,
    experiment_id: str | None = None,
    package_state: str = "none",
) -> cohort_subject_status:
    return cohort_subject_status(
        subject_id=subject.subject_id,
        infrastructure_subject=subject.infrastructure_subject,
        running=running,
        experiment_id=experiment_id,
        package_state=package_state,
    )


def test_clean_deployed_candidate_is_discovered_passively(monkeypatch, tmp_path: Path) -> None:
    cohort = _cohort()
    monkeypatch.setattr(qualification, "resolve_deployed_cohort", lambda *_: cohort)
    monkeypatch.setattr(
        qualification,
        "inspect_cohort",
        lambda *_: tuple(_status(subject) for subject in cohort.subjects),
    )
    baseline_id, subjects = qualification._require_clean_deployed_baseline(
        tmp_path,
        tmp_path / "runtime",
    )
    assert baseline_id == "gpt_oss_120b_cloud_candidate_v1"
    assert tuple(subjects) == ("hermes", "open-webui", "agent-zero")


def test_behavioral_qualification_translates_inconsistent_deployment_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_resolution(*_args, **_kwargs):
        raise qualification.infrastructure_error("stale package state")

    monkeypatch.setattr(qualification, "resolve_deployed_cohort", fail_resolution)
    with pytest.raises(
        qualification.llm_behavioral_qualification_error,
        match="acmm env set-llm <llm-id>",
    ):
        qualification._require_clean_deployed_baseline(tmp_path, tmp_path / "runtime")


def test_behavioral_qualification_requires_stopped_runtimes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cohort = _cohort()
    statuses = [_status(subject) for subject in cohort.subjects]
    statuses[1] = _status(cohort.subjects[1], running=True)
    monkeypatch.setattr(qualification, "resolve_deployed_cohort", lambda *_: cohort)
    monkeypatch.setattr(qualification, "inspect_cohort", lambda *_: tuple(statuses))
    with pytest.raises(
        qualification.llm_behavioral_qualification_error,
        match="requires stopped runtimes",
    ):
        qualification._require_clean_deployed_baseline(tmp_path, tmp_path / "runtime")


def test_behavioral_qualification_rejects_active_package(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cohort = _cohort()
    statuses = [_status(subject) for subject in cohort.subjects]
    statuses[0] = _status(
        cohort.subjects[0],
        experiment_id="some_experiment_v1",
        package_state="match",
    )
    monkeypatch.setattr(qualification, "resolve_deployed_cohort", lambda *_: cohort)
    monkeypatch.setattr(qualification, "inspect_cohort", lambda *_: tuple(statuses))
    with pytest.raises(
        qualification.llm_behavioral_qualification_error,
        match="clean experiment-independent baseline",
    ):
        qualification._require_clean_deployed_baseline(tmp_path, tmp_path / "runtime")


def test_parser_defaults_to_repeated_engineering_smoke() -> None:
    args = qualification.build_parser().parse_args([])
    assert args.iterations == 5
    assert args.cold_start is False
    assert args.run_timeout_seconds == 120.0
    assert args.startup_timeout_seconds == 120.0
    assert args.results_root == str(qualification.default_results_root())


def test_expected_agent_version_uses_manager_profile() -> None:
    manager = SimpleNamespace(profile=SimpleNamespace(expected_version="v-test"))
    assert qualification._expected_agent_version(manager) == "v-test"


class _fake_docker:
    def __init__(self) -> None:
        self.running = False

    def container_running(self) -> bool:
        return self.running


class _fake_manager:
    def __init__(self) -> None:
        self.docker = _fake_docker()
        self.profile = SimpleNamespace(expected_version="v-test")
        self.events: list[str] = []

    def up(self, *, timeout_seconds: float) -> None:
        self.events.append(f"up:{timeout_seconds}")
        self.docker.running = True

    def down(self) -> None:
        self.events.append("down")
        self.docker.running = False


def test_cold_start_parser_flag_and_runtime_start(monkeypatch) -> None:
    args = qualification.build_parser().parse_args(["--cold-start", "--iterations", "3"])
    assert args.cold_start is True
    assert args.iterations == 3
    manager = _fake_manager()
    times = iter((10.0, 12.5))
    monkeypatch.setattr(qualification.time, "monotonic", lambda: next(times))
    duration = qualification._start_cold_iteration(manager, timeout_seconds=90.0)
    assert duration == 2.5
    assert manager.docker.running is True
    assert manager.events == ["up:90.0"]


def test_cold_start_rejects_already_running_runtime() -> None:
    manager = _fake_manager()
    manager.docker.running = True
    with pytest.raises(
        qualification.llm_behavioral_qualification_error,
        match="unexpectedly running",
    ):
        qualification._start_cold_iteration(manager, timeout_seconds=90.0)
