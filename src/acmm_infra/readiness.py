from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from acmm_infra.common import doctor_report, infrastructure_error
from acmm_infra.profiles.common import profile_error
from acmm_infra.subject_managers import build_deployed_subject_manager


@dataclass(frozen=True)
class deployed_subject_doctor_result:
    infrastructure_subject: str
    experiment_id: str | None
    report: doctor_report | None
    error: str | None

    @property
    def is_ready(self) -> bool:
        return self.error is None and self.report is not None and self.report.is_ready


def doctor_deployed_subject(
    repo_root: str | Path,
    runtime_root: str | Path,
    infrastructure_subject: str,
    llm_baseline_id: str | None = None,
) -> deployed_subject_doctor_result:
    try:
        context = build_deployed_subject_manager(
            repo_root,
            runtime_root,
            infrastructure_subject,
            llm_baseline_id,
        )
    except (infrastructure_error, profile_error, OSError, ValueError) as exc:
        return deployed_subject_doctor_result(
            infrastructure_subject=infrastructure_subject,
            experiment_id=None,
            report=None,
            error=str(exc),
        )

    try:
        report = context.manager.doctor()
    except (infrastructure_error, OSError, ValueError) as exc:
        return deployed_subject_doctor_result(
            infrastructure_subject=infrastructure_subject,
            experiment_id=context.experiment_id,
            report=None,
            error=str(exc),
        )

    return deployed_subject_doctor_result(
        infrastructure_subject=infrastructure_subject,
        experiment_id=context.experiment_id,
        report=report,
        error=None,
    )
