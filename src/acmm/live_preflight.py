from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from acmm.subjects.catalog import subject_execution_binding
from acmm_infra.common import doctor_check
from acmm_infra.readiness import doctor_deployed_subject


@dataclass(frozen=True)
class subject_live_readiness:
    subject_id: str
    infrastructure_subject: str
    experiment_id: str | None
    checks: tuple[doctor_check, ...]
    error: str | None

    @property
    def is_ready(self) -> bool:
        return self.error is None and all(check.ok for check in self.checks)

    @property
    def failed_checks(self) -> tuple[doctor_check, ...]:
        return tuple(check for check in self.checks if not check.ok)


@dataclass(frozen=True)
class live_readiness_preflight_result:
    subjects: tuple[subject_live_readiness, ...]

    @property
    def is_ready(self) -> bool:
        return all(subject.is_ready for subject in self.subjects)


def inspect_live_readiness(
    *,
    repo_root: Path,
    runtime_root: Path,
    subject_bindings: Mapping[str, subject_execution_binding],
) -> live_readiness_preflight_result:
    observations: list[subject_live_readiness] = []

    for subject_id, binding in subject_bindings.items():
        result = doctor_deployed_subject(
            repo_root,
            runtime_root,
            binding.infrastructure_subject,
        )
        checks = result.report.checks if result.report is not None else ()
        observations.append(
            subject_live_readiness(
                subject_id=subject_id,
                infrastructure_subject=binding.infrastructure_subject,
                experiment_id=result.experiment_id,
                checks=checks,
                error=result.error,
            )
        )

    return live_readiness_preflight_result(subjects=tuple(observations))
