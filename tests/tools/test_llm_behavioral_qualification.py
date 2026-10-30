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


def _status(subject: cohort_subject, *, running: bool = False) -> cohort_subject_status:
    return cohort_subject_status(
        subject_id=subject.subject_id,
        infrastructure_subject=subject.infrastructure_subject,
        running=running,
        experiment_id=None,
        application_scope=None,
        apparatus_state="none",
        source_sha256=None,
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
    assert subjects["hermes"].subject_id == "hermes_llm_qualification"




def test_behavioral_qualification_translates_inconsistent_deployment_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_resolution(*_args, **_kwargs):
        raise qualification.infrastructure_error(
            "Deployed experiment 'magic_number_tool_use_v1' does not resolve exactly "
            "one subject for infrastructure 'open-webui'."
        )

    monkeypatch.setattr(qualification, "resolve_deployed_cohort", fail_resolution)

    with pytest.raises(
        qualification.llm_behavioral_qualification_error,
        match="Run 'acmm env apply'",
    ):
        qualification._require_clean_deployed_baseline(
            tmp_path,
            tmp_path / "runtime",
        )

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
        qualification._require_clean_deployed_baseline(
            tmp_path,
            tmp_path / "runtime",
        )


def test_behavioral_qualification_rejects_active_apparatus(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cohort = _cohort()
    statuses = [_status(subject) for subject in cohort.subjects]
    statuses[0] = cohort_subject_status(
        subject_id=cohort.subjects[0].subject_id,
        infrastructure_subject="hermes",
        running=False,
        experiment_id="magic_number_tool_use_v1",
        application_scope="tools",
        apparatus_state="match",
        source_sha256="abc",
    )
    monkeypatch.setattr(qualification, "resolve_deployed_cohort", lambda *_: cohort)
    monkeypatch.setattr(qualification, "inspect_cohort", lambda *_: tuple(statuses))

    with pytest.raises(
        qualification.llm_behavioral_qualification_error,
        match="clean experiment-independent baseline",
    ):
        qualification._require_clean_deployed_baseline(
            tmp_path,
            tmp_path / "runtime",
        )


def test_parser_defaults_to_repeated_engineering_smoke() -> None:
    args = qualification.build_parser().parse_args([])

    assert args.iterations == 5
    assert args.cold_start is False
    assert args.run_timeout_seconds == 120.0
    assert args.startup_timeout_seconds == 120.0
    assert args.results_root == "results"


def test_expected_agent_version_uses_manager_profile() -> None:
    manager = SimpleNamespace(profile=SimpleNamespace(expected_version="v-test"))
    assert qualification._expected_agent_version(manager) == "v-test"


def test_cold_start_parser_flag_and_runtime_start(monkeypatch) -> None:
    args = qualification.build_parser().parse_args(["--cold-start", "--iterations", "3"])
    assert args.cold_start is True
    assert args.iterations == 3

    events: list[str] = []
    manager = _fake_manager("cold", events)
    times = iter((10.0, 12.5))
    monkeypatch.setattr(qualification.time, "monotonic", lambda: next(times))

    duration = qualification._start_cold_iteration(manager, timeout_seconds=90.0)

    assert duration == 2.5
    assert manager.docker.running is True
    assert events == ["cold:up:90.0"]


def test_cold_start_rejects_already_running_runtime() -> None:
    events: list[str] = []
    manager = _fake_manager("cold", events)
    manager.docker.running = True

    with pytest.raises(
        qualification.llm_behavioral_qualification_error,
        match="unexpectedly running",
    ):
        qualification._start_cold_iteration(manager, timeout_seconds=90.0)


class _fake_docker:
    def __init__(self) -> None:
        self.running = False

    def container_running(self) -> bool:
        return self.running


class _fake_manager:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events
        self.docker = _fake_docker()
        self.profile = SimpleNamespace(expected_version="v-test")

    def up(self, *, timeout_seconds: float) -> None:
        self.events.append(f"{self.name}:up:{timeout_seconds}")
        self.docker.running = True

    def down(self) -> None:
        self.events.append(f"{self.name}:down")
        self.docker.running = False

    def apply_tools(self, experiment_id, *, timeout_seconds: float) -> None:
        self.events.append(
            f"{self.name}:tools:{experiment_id or 'baseline'}:{timeout_seconds}"
        )


def test_hermes_smoke_tools_install_before_start_and_restore_after_stop(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    experiment_manager = _fake_manager("experiment", events)
    baseline_manager = _fake_manager("baseline", events)

    def build_manager(_repo, _runtime, _subject, experiment_id, _baseline):
        manager = experiment_manager if experiment_id is not None else baseline_manager
        return SimpleNamespace(manager=manager)

    monkeypatch.setattr(qualification, "build_subject_manager", build_manager)

    manager, _ = qualification._apply_smoke_tools(
        tmp_path,
        tmp_path / "runtime",
        "hermes",
        "candidate",
        timeout_seconds=90.0,
    )
    qualification._restore_baseline_tools(
        tmp_path,
        tmp_path / "runtime",
        "hermes",
        "candidate",
        manager,
        timeout_seconds=90.0,
    )

    assert events == [
        "experiment:tools:magic_number_tool_use_v1:90.0",
        "experiment:up:90.0",
        "experiment:down",
        "baseline:tools:baseline:90.0",
    ]


def test_open_webui_smoke_tools_install_and_restore_while_running(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    experiment_manager = _fake_manager("experiment", events)
    baseline_manager = _fake_manager("baseline", events)
    baseline_manager.docker = experiment_manager.docker

    def build_manager(_repo, _runtime, _subject, experiment_id, _baseline):
        manager = experiment_manager if experiment_id is not None else baseline_manager
        return SimpleNamespace(manager=manager)

    monkeypatch.setattr(qualification, "build_subject_manager", build_manager)

    manager, _ = qualification._apply_smoke_tools(
        tmp_path,
        tmp_path / "runtime",
        "open-webui",
        "candidate",
        timeout_seconds=90.0,
    )
    qualification._restore_baseline_tools(
        tmp_path,
        tmp_path / "runtime",
        "open-webui",
        "candidate",
        manager,
        timeout_seconds=90.0,
    )

    assert events == [
        "experiment:up:90.0",
        "experiment:tools:magic_number_tool_use_v1:90.0",
        "baseline:tools:baseline:90.0",
        "experiment:down",
    ]


def test_open_webui_restore_starts_temporarily_when_runtime_is_stopped(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    active_manager = _fake_manager("experiment", events)
    baseline_manager = _fake_manager("baseline", events)

    def build_manager(_repo, _runtime, _subject, experiment_id, _baseline):
        assert experiment_id is None
        return SimpleNamespace(manager=baseline_manager)

    monkeypatch.setattr(qualification, "build_subject_manager", build_manager)

    qualification._restore_baseline_tools(
        tmp_path,
        tmp_path / "runtime",
        "open-webui",
        "candidate",
        active_manager,
        timeout_seconds=90.0,
    )

    assert events == [
        "baseline:up:90.0",
        "baseline:tools:baseline:90.0",
        "baseline:down",
    ]
    assert baseline_manager.docker.running is False


def test_smoke_apparatus_composes_with_candidate_model_without_rebinding_history(
    tmp_path: Path,
) -> None:
    baseline = "gpt_oss_120b_cloud_candidate_v1"
    for infrastructure_subject in ("hermes", "open-webui", "agent-zero"):
        context = qualification.build_subject_manager(
            _REPO_ROOT,
            tmp_path / "runtime",
            infrastructure_subject,
            "magic_number_tool_use_v1",
            baseline,
        )

        assert context.llm_baseline_id == baseline
        assert context.manager.profile.model_default == "gpt-oss:120b-cloud"
        if infrastructure_subject == "hermes":
            assert context.manager.profile.plugins
        elif infrastructure_subject == "open-webui":
            assert context.manager.profile.tools
        else:
            assert context.manager.profile.plugins
