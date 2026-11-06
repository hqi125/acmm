from __future__ import annotations

from pathlib import Path

import pytest

from acmm.cli import _build_parser as build_acmm_parser
from acmm_infra.cli import _build_parser as build_infra_parser


EXPERIMENT = "retired_experiment_v1"


def test_acmm_exposes_environment_command_family() -> None:
    parser = build_acmm_parser()

    args = parser.parse_args(
        [
            "env",
            "set-llm",
            "gpt_oss_120b_cloud_v1",
            "--subject",
            "hermes_gpt_oss120b",
        ]
    )
    assert args.command == "env"
    assert args.env_command == "set-llm"
    assert args.subjects == ["hermes_gpt_oss120b"]
    assert args.timeout_seconds == 120.0
    assert args.llm_id == "gpt_oss_120b_cloud_v1"
    assert not hasattr(args, "experiment_id")
    assert not hasattr(args, "package_path")

    for retired in (
        ["apply"],
        ["set-llm", "gpt_oss_120b_cloud_v1", "--experiment", EXPERIMENT],
        ["set-llm", "gpt_oss_120b_cloud_v1", "--package", "/tmp/example-package"],
        ["set-llm", "gpt_oss_120b_cloud_v1", "--llm-baseline", "candidate_v1"],
        ["set-llm", "gpt_oss_120b_cloud_v1", "--repo-root", "/tmp/repo"],
        ["status", "--runtime-root", "/tmp/runtime"],
    ):
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["env", *retired])
        assert exc_info.value.code == 2

    args = parser.parse_args(["env", "status", "--json"])
    assert args.command == "env"
    assert args.env_command == "status"
    assert args.json is True
    assert not hasattr(args, "llm_baseline_id")

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(
            [
                "env",
                "status",
                "--llm-baseline",
                "gpt_oss_120b_cloud_candidate_v1",
            ]
        )
    assert exc_info.value.code == 2

    args = parser.parse_args(["env", "doctor", "--start-if-needed"])
    assert args.command == "env"
    assert args.env_command == "doctor"
    assert args.start_if_needed is True
    assert args.startup_timeout_seconds == 120.0
    assert not hasattr(args, "subjects")

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(
            [
                "env",
                "doctor",
                "--subject",
                "agent_zero_nemotron30b",
            ]
        )
    assert exc_info.value.code == 2

    args = parser.parse_args(["env", "stop", "--subject", "hermes_nemotron30b"])
    assert args.command == "env"
    assert args.env_command == "stop"
    assert args.subjects == ["hermes_nemotron30b"]


def test_environment_set_llm_reconciles_without_experiment(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from acmm import environment_cli

    monkeypatch.setattr(environment_cli, "default_repo_root", lambda: tmp_path)
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(tmp_path / "runtime"))
    from acmm_infra.cohort import (
        cohort_subject,
        cohort_subject_status,
        experiment_cohort,
    )

    subject = cohort_subject(
        "hermes_llm_qualification",
        "hermes",
        "candidate_v1",
    )
    observed = {}

    def fake_resolve(repo_root, selected, *, llm_baseline_id=None):
        observed["repo_root"] = Path(repo_root)
        observed["selected"] = selected
        observed["llm_baseline_id"] = llm_baseline_id
        return experiment_cohort(None, None, (subject,))

    def fake_apply(repo_root, runtime_root, cohort, **kwargs):
        observed["runtime_root"] = Path(runtime_root)
        observed["cohort"] = cohort
        del repo_root, kwargs
        return (
            cohort_subject_status(
                subject_id=subject.subject_id,
                infrastructure_subject=subject.infrastructure_subject,
                running=False,
                experiment_id=None,
                package_state="none",
            ),
        )

    monkeypatch.setattr(environment_cli, "resolve_baseline_cohort", fake_resolve)
    monkeypatch.setattr(environment_cli, "apply_cohort", fake_apply)

    parser = build_acmm_parser()
    args = parser.parse_args(
        [
            "env",
            "set-llm",
            "candidate_v1",
            "--subject",
            subject.subject_id,
        ]
    )
    assert args.handler(args) == 0
    assert observed["selected"] == (subject.subject_id,)
    assert observed["llm_baseline_id"] == "candidate_v1"
    assert observed["cohort"].experiment_id is None

    output = capsys.readouterr().out
    assert "LLM:                candidate_v1" in output
    assert "Experiment Package" not in output


def test_acmm_infra_root_accepts_only_subject_backends() -> None:
    parser = build_infra_parser()

    for command in ("apply", "status", "doctor"):
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args([command])
        assert exc_info.value.code == 2

    for backend in ("hermes", "open-webui", "agent-zero"):
        args = parser.parse_args([backend, "status"])
        assert args.command == backend


def test_environment_status_reports_llm_context_observability(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from acmm import environment_cli

    monkeypatch.setattr(environment_cli, "default_repo_root", lambda: tmp_path)
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(tmp_path / "runtime"))
    from acmm_infra.cohort import (
        cohort_llm_observation,
        cohort_subject,
        cohort_subject_status,
        experiment_cohort,
    )

    hermes = cohort_subject(
        "hermes_nemotron30b",
        "hermes",
        "nemotron_3_nano_30b_cloud_v1",
    )
    open_webui = cohort_subject(
        "open_webui_nemotron30b",
        "open-webui",
        "nemotron_3_nano_30b_cloud_v1",
    )
    monkeypatch.setattr(
        environment_cli,
        "resolve_deployed_cohort",
        lambda repo_root, runtime_root, selected=(), **kwargs: experiment_cohort(
            None,
            None,
            (hermes, open_webui),
        ),
    )
    monkeypatch.setattr(
        environment_cli,
        "inspect_cohort",
        lambda *args, **kwargs: (
            cohort_subject_status(
                subject_id=hermes.subject_id,
                infrastructure_subject=hermes.infrastructure_subject,
                running=True,
                experiment_id=None,
                package_state="none",
                llm=cohort_llm_observation(
                    expected_baseline_id=hermes.llm_baseline_id,
                    deployed_baseline_id=hermes.llm_baseline_id,
                    model_name="nemotron-3-nano:30b-cloud",
                    logical_provider="ollama_cloud",
                    minimum_context_window=64000,
                    requested_context_window=64000,
                    observed_model_name="nemotron-3-nano:30b-cloud",
                    observed_context_window=64000,
                    model_observation_source="hermes_live_config",
                    context_observation_source="hermes_live_config:context_window",
                    context_observation_kind="configured_effective",
                    model_matches_baseline=True,
                    context_meets_minimum=True,
                    context_matches_requested=True,
                ),
            ),
            cohort_subject_status(
                subject_id=open_webui.subject_id,
                infrastructure_subject=open_webui.infrastructure_subject,
                running=True,
                experiment_id=None,
                package_state="none",
                llm=cohort_llm_observation(
                    expected_baseline_id=open_webui.llm_baseline_id,
                    deployed_baseline_id=open_webui.llm_baseline_id,
                    model_name="nemotron-3-nano:30b-cloud",
                    logical_provider="ollama_cloud",
                    minimum_context_window=64000,
                    requested_context_window=None,
                    observed_model_name="nemotron-3-nano:30b-cloud",
                    observed_context_window=131072,
                    model_observation_source="open_webui_model_catalog",
                    context_observation_source=(
                        "open_webui_model_catalog:info.context_length"
                    ),
                    context_observation_kind="model_capability",
                    model_matches_baseline=True,
                    context_meets_minimum=True,
                    context_matches_requested=None,
                ),
            ),
        ),
    )

    parser = build_acmm_parser()
    args = parser.parse_args(
        [
            "env",
            "status",
        ]
    )
    assert args.handler(args) == 0

    output = capsys.readouterr().out
    assert "LLM:                nemotron_3_nano_30b_cloud_v1" in output
    assert "context requested:  not controlled" in output
    assert "context kind:       configured_effective" in output
    assert "context kind:       model_capability" in output
    assert "configured/effective: hermes_nemotron30b=64000" in output
    assert "capability metadata:  open_webui_nemotron30b=131072" in output


def test_environment_status_explains_unknown_context(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from acmm import environment_cli

    monkeypatch.setattr(environment_cli, "default_repo_root", lambda: tmp_path)
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(tmp_path / "runtime"))
    from acmm_infra.cohort import (
        cohort_llm_observation,
        cohort_subject,
        cohort_subject_status,
        experiment_cohort,
    )

    subject = cohort_subject(
        "hermes_nemotron30b",
        "hermes",
        "nemotron_3_nano_30b_cloud_v1",
    )
    monkeypatch.setattr(
        environment_cli,
        "resolve_deployed_cohort",
        lambda repo_root, runtime_root, selected=(), **kwargs: experiment_cohort(
            None,
            None,
            (subject,),
        ),
    )
    monkeypatch.setattr(
        environment_cli,
        "inspect_cohort",
        lambda *args, **kwargs: (
            cohort_subject_status(
                subject_id=subject.subject_id,
                infrastructure_subject=subject.infrastructure_subject,
                running=False,
                experiment_id=None,
                package_state="none",
                llm=cohort_llm_observation(
                    expected_baseline_id=subject.llm_baseline_id,
                    deployed_baseline_id=subject.llm_baseline_id,
                    model_name="nemotron-3-nano:30b-cloud",
                    logical_provider="ollama_cloud",
                    minimum_context_window=64000,
                    requested_context_window=64000,
                    observed_model_name=None,
                    observed_context_window=None,
                    model_observation_source=None,
                    context_observation_source=None,
                    context_observation_kind="unknown",
                    model_matches_baseline=None,
                    context_meets_minimum=None,
                    context_matches_requested=None,
                ),
            ),
        ),
    )

    parser = build_acmm_parser()
    args = parser.parse_args(
        [
            "env",
            "status",
        ]
    )
    assert args.handler(args) == 0

    output = capsys.readouterr().out
    assert "context observed:   N/A" in output
    assert "runtime stopped" in output
    assert "context-window observability:" not in output


def test_environment_doctor_reports_subject_progress(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from acmm import environment_cli

    monkeypatch.setattr(environment_cli, "default_repo_root", lambda: tmp_path)
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(tmp_path / "runtime"))
    from acmm_infra.cohort import (
        cohort_doctor_result,
        cohort_llm_observation,
        cohort_subject,
        cohort_subject_status,
        experiment_cohort,
    )
    from acmm_infra.common import doctor_check, doctor_report
    from acmm_infra.readiness import deployed_subject_doctor_result

    subject = cohort_subject("hermes_nemotron30b", "hermes")
    monkeypatch.setattr(
        environment_cli,
        "resolve_deployed_cohort",
        lambda repo_root, runtime_root, selected=(), **kwargs: experiment_cohort(None, None, (subject,)),
    )
    monkeypatch.setattr(
        environment_cli,
        "inspect_cohort",
        lambda *args, **kwargs: (
            cohort_subject_status(
                subject_id=subject.subject_id,
                infrastructure_subject=subject.infrastructure_subject,
                running=False,
                experiment_id=EXPERIMENT,
                package_state="match",
            ),
        ),
    )

    def fake_doctor_cohort(*args, progress=None, **kwargs):
        assert progress is not None
        for event in (
            "begin",
            "runtime-stopped",
            "starting",
            "started",
            "checking",
            "restoring",
            "restored",
            "complete-ready",
        ):
            progress(1, 1, subject, event)
        return (
            cohort_doctor_result(
                subject=subject,
                doctor=deployed_subject_doctor_result(
                    infrastructure_subject="hermes",
                    experiment_id=EXPERIMENT,
                    report=doctor_report(
                        (
                            doctor_check("ready", True, "ready"),
                            doctor_check(
                                "integration:example_discovery",
                                True,
                                "discovered expected tools",
                            ),
                        )
                    ),
                    error=None,
                ),
                deployment_state={
                    "application_scope": "experiment",
                    "source_sha256": "abc",
                },
                initially_running=False,
                started_by_acmm=True,
                llm=cohort_llm_observation(
                    expected_baseline_id=subject.llm_baseline_id,
                    deployed_baseline_id=subject.llm_baseline_id,
                    model_name="nemotron-3-nano:30b-cloud",
                    logical_provider="ollama_cloud",
                    minimum_context_window=64000,
                    requested_context_window=64000,
                    observed_model_name="nemotron-3-nano:30b-cloud",
                    observed_context_window=64000,
                    model_observation_source="hermes_live_config",
                    context_observation_source="hermes_live_config:context_window",
                    context_observation_kind="configured_effective",
                    model_matches_baseline=True,
                    context_meets_minimum=True,
                    context_matches_requested=True,
                ),
            ),
        )

    monkeypatch.setattr(environment_cli, "doctor_cohort", fake_doctor_cohort)

    parser = build_acmm_parser()
    args = parser.parse_args(
        [
            "env",
            "doctor",
            "--start-if-needed",
        ]
    )
    assert args.handler(args) == 0

    output = capsys.readouterr().out
    assert "ACMM environment doctor progress:" in output
    assert "[1/1] hermes_nemotron30b" in output
    assert "startup:        starting temporarily..." in output
    assert "doctor:         running checks..." in output
    assert "cleanup:        restoring stopped state..." in output
    assert "context observed:   64000" in output
    assert output.count("LLM:                gpt_oss_120b_cloud_v1") == 1
    assert "integration checks:" in output
    assert (
        "integration:example_discovery: READY - discovered expected tools"
        in output
    )
    assert "configured/effective: hermes_nemotron30b=64000" in output
    assert "result:         READY" in output


def test_environment_doctor_accepts_converged_baseline_context(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from acmm import environment_cli

    monkeypatch.setattr(environment_cli, "default_repo_root", lambda: tmp_path)
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(tmp_path / "runtime"))
    from acmm_infra.cohort import (
        cohort_doctor_result,
        cohort_subject,
        cohort_subject_status,
        experiment_cohort,
    )
    from acmm_infra.common import doctor_check, doctor_report
    from acmm_infra.readiness import deployed_subject_doctor_result

    subject = cohort_subject(
        "hermes_nemotron30b",
        "hermes",
        "nemotron_3_nano_30b_cloud_v1",
    )
    monkeypatch.setattr(
        environment_cli,
        "resolve_deployed_cohort",
        lambda repo_root, runtime_root, selected=(), **kwargs: experiment_cohort(None, None, (subject,)),
    )
    monkeypatch.setattr(
        environment_cli,
        "inspect_cohort",
        lambda *args, **kwargs: (
            cohort_subject_status(
                subject_id=subject.subject_id,
                infrastructure_subject=subject.infrastructure_subject,
                running=False,
                experiment_id=None,
                package_state="none",
            ),
        ),
    )
    monkeypatch.setattr(
        environment_cli,
        "doctor_cohort",
        lambda *args, **kwargs: (
            cohort_doctor_result(
                subject=subject,
                doctor=deployed_subject_doctor_result(
                    infrastructure_subject="hermes",
                    experiment_id=None,
                    report=doctor_report(
                        (doctor_check("ready", True, "ready"),)
                    ),
                    error=None,
                ),
                deployment_state={
                    "application_scope": None,
                    "source_sha256": None,
                },
                initially_running=False,
                started_by_acmm=False,
            ),
        ),
    )

    parser = build_acmm_parser()
    args = parser.parse_args(
        [
            "env",
            "doctor",
        ]
    )
    assert args.handler(args) == 0

    output = capsys.readouterr().out
    assert "state:      baseline" in output
    assert "readiness:         READY" in output
    assert "experiment-independent state" not in output
    assert output.rstrip().endswith("READY")


def test_environment_stop_reports_integration_resources(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from acmm import environment_cli
    from acmm.experiment_package import integration_resource_down_result
    from acmm_infra.cohort import (
        cohort_subject,
        cohort_subject_status,
        experiment_cohort,
        cohort_stop_result,
    )

    subject = cohort_subject(
        "hermes_gpt_oss120b",
        "hermes",
        "gpt_oss_120b_cloud_v1",
    )
    monkeypatch.setattr(environment_cli, "default_repo_root", lambda: tmp_path)
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(
        environment_cli,
        "resolve_deployed_cohort",
        lambda *args, **kwargs: experiment_cohort(
            "package_fixture_v1",
            None,
            (subject,),
        ),
    )
    monkeypatch.setattr(
        environment_cli,
        "stop_cohort",
        lambda *args, **kwargs: (
            cohort_stop_result(
                subject_id=subject.subject_id,
                infrastructure_subject=subject.infrastructure_subject,
                initial_running=False,
                stopped=False,
                error=None,
            ),
        ),
    )
    monkeypatch.setattr(
        environment_cli,
        "inspect_cohort",
        lambda *args, **kwargs: (
            cohort_subject_status(
                subject_id=subject.subject_id,
                infrastructure_subject=subject.infrastructure_subject,
                running=False,
                experiment_id="package_fixture_v1",
                package_sha256="abc123",
                package_state="match",
            ),
        ),
    )
    monkeypatch.setattr(
        environment_cli,
        "open_staged_experiment_package",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        environment_cli,
        "down_staged_integration_resources",
        lambda *args, **kwargs: (
            integration_resource_down_result(
                integration_id="github_mcp",
                initially_running=True,
                running_after=False,
                value={"container_present": False},
                error=None,
            ),
        ),
    )

    parser = build_acmm_parser()
    args = parser.parse_args(["env", "stop"])
    assert args.handler(args) == 0

    output = capsys.readouterr().out
    assert "integration resources:" in output
    assert "github_mcp:" in output
    assert "initial state:  running" in output
    assert "result:         stopped" in output
    assert "container:      absent" in output
