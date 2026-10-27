from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_DEFAULT_ORDERING = "subject_scenario_repetition"
_INTERLEAVED_ORDERING = "subject_repetition_scenario"


@dataclass(frozen=True)
class planned_run:
    run_index: int
    subject_id: str
    scenario_id: str
    repetition: int


@dataclass(frozen=True)
class experiment_plan:
    experiment_id: str
    runs: tuple[planned_run, ...]

    @property
    def run_count(self) -> int:
        return len(self.runs)


def build_experiment_plan(
    contract: dict[str, Any],
) -> experiment_plan:
    """
    Expand a validated Experiment Contract into deterministic planned runs.

    The default ordering preserves the historical Contract v0.1 expansion:
    subject, then scenario, then repetition. A Contract may instead request
    subject, then repetition, then scenario to interleave scenario conditions
    within each subject.

    The caller is responsible for validating the Contract before planning.
    """

    protocol = contract["run_protocol"]
    repetitions = protocol["repetitions"]
    ordering = protocol.get("ordering", _DEFAULT_ORDERING)

    if ordering == _DEFAULT_ORDERING:
        coordinates = (
            (subject, scenario, repetition)
            for subject in contract["subjects"]
            for scenario in contract["scenarios"]
            for repetition in range(1, repetitions + 1)
        )
    elif ordering == _INTERLEAVED_ORDERING:
        coordinates = (
            (subject, scenario, repetition)
            for subject in contract["subjects"]
            for repetition in range(1, repetitions + 1)
            for scenario in contract["scenarios"]
        )
    else:
        raise ValueError(f"Unsupported run ordering {ordering!r}.")

    runs = tuple(
        planned_run(
            run_index=run_index,
            subject_id=subject["id"],
            scenario_id=scenario["id"],
            repetition=repetition,
        )
        for run_index, (subject, scenario, repetition) in enumerate(coordinates)
    )

    return experiment_plan(
        experiment_id=contract["experiment"]["id"],
        runs=runs,
    )
