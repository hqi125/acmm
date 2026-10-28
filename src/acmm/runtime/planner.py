from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_DEFAULT_ORDERING = "subject_scenario_repetition"
_INTERLEAVED_ORDERING = "subject_repetition_scenario"
_DEFAULT_SCENARIO_ORDERING = "declaration"
_CYCLIC_SCENARIO_ORDERING = "cyclic_rotation"


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
    within each subject. Interleaved plans may additionally rotate declaration
    order across repetitions to counterbalance scenario position.

    The caller is responsible for validating the Contract before planning.
    """

    protocol = contract["run_protocol"]
    repetitions = protocol["repetitions"]
    ordering = protocol.get("ordering", _DEFAULT_ORDERING)
    scenario_ordering = protocol.get(
        "scenario_ordering",
        _DEFAULT_SCENARIO_ORDERING,
    )

    if ordering == _DEFAULT_ORDERING:
        if scenario_ordering != _DEFAULT_SCENARIO_ORDERING:
            raise ValueError(
                "run_protocol.scenario_ordering requires "
                "ordering='subject_repetition_scenario'."
            )

        coordinates = (
            (subject, scenario, repetition)
            for subject in contract["subjects"]
            for scenario in contract["scenarios"]
            for repetition in range(1, repetitions + 1)
        )
    elif ordering == _INTERLEAVED_ORDERING:
        coordinates = _build_interleaved_coordinates(
            subjects=contract["subjects"],
            scenarios=contract["scenarios"],
            repetitions=repetitions,
            scenario_ordering=scenario_ordering,
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


def _build_interleaved_coordinates(
    *,
    subjects: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    repetitions: int,
    scenario_ordering: str,
) -> Iterable[tuple[dict[str, Any], dict[str, Any], int]]:
    if scenario_ordering == _DEFAULT_SCENARIO_ORDERING:
        for subject in subjects:
            for repetition in range(1, repetitions + 1):
                for scenario in scenarios:
                    yield subject, scenario, repetition
        return

    if scenario_ordering == _CYCLIC_SCENARIO_ORDERING:
        scenario_count = len(scenarios)

        for subject_index, subject in enumerate(subjects):
            for repetition in range(1, repetitions + 1):
                offset = (subject_index + repetition - 1) % scenario_count

                for scenario_index in range(scenario_count):
                    scenario = scenarios[(offset + scenario_index) % scenario_count]
                    yield subject, scenario, repetition
        return

    raise ValueError(f"Unsupported scenario ordering {scenario_ordering!r}.")
