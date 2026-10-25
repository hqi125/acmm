from __future__ import annotations


import pytest

from acmm.cli import _build_parser as build_acmm_parser
from acmm_infra.cli import _build_parser as build_infra_parser


EXPERIMENT = "controlled_tool_use_multiscenario_v1"


def test_acmm_exposes_environment_command_family() -> None:
    parser = build_acmm_parser()

    args = parser.parse_args(
        [
            "env",
            "apply",
            "--experiment",
            EXPERIMENT,
            "--subject",
            "hermes_nemotron30b",
        ]
    )
    assert args.command == "env"
    assert args.env_command == "apply"
    assert args.experiment_id == EXPERIMENT
    assert args.subjects == ["hermes_nemotron30b"]
    assert args.timeout_seconds == 120.0

    args = parser.parse_args(["env", "status", "--json"])
    assert args.command == "env"
    assert args.env_command == "status"
    assert args.json is True

    args = parser.parse_args(
        [
            "env",
            "doctor",
            "--subject",
            "agent_zero_nemotron30b",
            "--start-if-needed",
        ]
    )
    assert args.command == "env"
    assert args.env_command == "doctor"
    assert args.subjects == ["agent_zero_nemotron30b"]
    assert args.start_if_needed is True
    assert args.startup_timeout_seconds == 120.0

    args = parser.parse_args(["env", "stop", "--subject", "hermes_nemotron30b"])
    assert args.command == "env"
    assert args.env_command == "stop"
    assert args.subjects == ["hermes_nemotron30b"]


def test_acmm_infra_root_accepts_only_subject_backends() -> None:
    parser = build_infra_parser()

    for command in ("apply", "status", "doctor"):
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args([command])
        assert exc_info.value.code == 2

    for backend in ("hermes", "open-webui", "agent-zero"):
        args = parser.parse_args([backend, "status"])
        assert args.command == backend


def test_environment_doctor_reports_subject_progress(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from acmm import environment_cli
    from acmm_infra.cohort import (
        cohort_doctor_result,
        cohort_subject,
        cohort_subject_status,
        experiment_cohort,
    )
    from acmm_infra.common import doctor_check, doctor_report
    from acmm_infra.readiness import deployed_subject_doctor_result

    subject = cohort_subject("hermes_nemotron30b", "hermes")
    monkeypatch.setattr(
        environment_cli,
        "resolve_baseline_cohort",
        lambda selected: experiment_cohort(None, None, (subject,)),
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
                application_scope="experiment",
                apparatus_state="match",
                source_sha256="abc",
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
                    report=doctor_report((doctor_check("ready", True, "ready"),)),
                    error=None,
                ),
                deployment_state={
                    "application_scope": "experiment",
                    "source_sha256": "abc",
                },
                initially_running=False,
                started_by_acmm=True,
            ),
        )

    monkeypatch.setattr(environment_cli, "doctor_cohort", fake_doctor_cohort)

    parser = build_acmm_parser()
    args = parser.parse_args(
        [
            "env",
            "doctor",
            "--start-if-needed",
            "--repo-root",
            str(tmp_path),
            "--runtime-root",
            str(tmp_path / "runtime"),
        ]
    )
    assert args.handler(args) == 0

    output = capsys.readouterr().out
    assert "ACMM environment doctor progress:" in output
    assert "[1/1] hermes_nemotron30b" in output
    assert "startup:        starting temporarily..." in output
    assert "doctor:         running checks..." in output
    assert "cleanup:        restoring stopped state..." in output
    assert "result:         READY" in output
