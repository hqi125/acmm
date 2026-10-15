from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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

    Ordering is contract order:
    subject order, then scenario order, then repetition ascending from 1.

    The caller is responsible for validating the Contract before planning.
    """

    repetitions = contract["run_protocol"]["repetitions"]

    runs: list[planned_run] = []
    run_index = 0

    for subject in contract["subjects"]:
        for scenario in contract["scenarios"]:
            for repetition in range(1, repetitions + 1):
                runs.append(
                    planned_run(
                        run_index=run_index,
                        subject_id=subject["id"],
                        scenario_id=scenario["id"],
                        repetition=repetition,
                    )
                )
                run_index += 1

    return experiment_plan(
        experiment_id=contract["experiment"]["id"],
        runs=tuple(runs),
    )
