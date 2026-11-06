from pathlib import Path

from acmm.preflight.live import inspect_live_readiness
from acmm.subjects.catalog import lookup_subject_execution_binding
from acmm_infra.common import doctor_check, doctor_report
from acmm_infra.readiness import (
    deployed_subject_doctor_result,
    doctor_deployed_subject,
)


def _binding(subject_id: str):
    binding = lookup_subject_execution_binding(subject_id)
    assert binding is not None
    return binding


def test_live_preflight_reports_failed_doctor_checks(monkeypatch, tmp_path: Path) -> None:
    def fake_doctor(repo_root, runtime_root, infrastructure_subject):
        del repo_root, runtime_root
        assert infrastructure_subject == "hermes"
        return deployed_subject_doctor_result(
            infrastructure_subject="hermes",
            experiment_id="package_fixture_v1",
            report=doctor_report(
                checks=(
                    doctor_check("container_running", False, "container is stopped"),
                    doctor_check("plugin_sync", True, "exact"),
                )
            ),
            error=None,
        )

    monkeypatch.setattr("acmm.preflight.live.doctor_deployed_subject", fake_doctor)

    result = inspect_live_readiness(
        repo_root=tmp_path,
        runtime_root=tmp_path / "runtime",
        subject_bindings={"hermes_nemotron30b": _binding("hermes_nemotron30b")},
    )

    assert not result.is_ready
    subject = result.subjects[0]
    assert not subject.is_ready
    assert [check.name for check in subject.failed_checks] == ["container_running"]


def test_live_preflight_checks_only_supplied_subjects(monkeypatch, tmp_path: Path) -> None:
    observed: list[str] = []

    def fake_doctor(repo_root, runtime_root, infrastructure_subject):
        del repo_root, runtime_root
        observed.append(infrastructure_subject)
        return deployed_subject_doctor_result(
            infrastructure_subject=infrastructure_subject,
            experiment_id="package_fixture_v1",
            report=doctor_report(checks=()),
            error=None,
        )

    monkeypatch.setattr("acmm.preflight.live.doctor_deployed_subject", fake_doctor)

    result = inspect_live_readiness(
        repo_root=tmp_path,
        runtime_root=tmp_path / "runtime",
        subject_bindings={
            "open_webui_nemotron30b": _binding("open_webui_nemotron30b")
        },
    )

    assert result.is_ready
    assert observed == ["open-webui"]


def test_infrastructure_readiness_rejects_unknown_subject(tmp_path: Path) -> None:
    result = doctor_deployed_subject(
        tmp_path,
        tmp_path / "runtime",
        "unknown-subject",
    )

    assert not result.is_ready
    assert result.report is None
    assert result.error is not None
    assert "no infrastructure readiness builder" in result.error
