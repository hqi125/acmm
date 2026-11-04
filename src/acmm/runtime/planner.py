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


def _parse_index_selectors(
    selectors: list[str] | None,
    *,
    maximum: int,
    option_name: str,
    range_description: str,
) -> set[int] | None:
    if selectors is None:
        return None

    selected: set[int] = set()

    for selector in selectors:
        if selector.isdecimal():
            start = int(selector)
            end = start
        else:
            parts = selector.split("-")

            if (
                len(parts) != 2
                or not parts[0].isdecimal()
                or not parts[1].isdecimal()
            ):
                raise ValueError(
                    f"{option_name} must be a non-negative index or inclusive "
                    f"range START-END; got '{selector}'."
                )

            start = int(parts[0])
            end = int(parts[1])

            if start > end:
                raise ValueError(
                    f"{option_name} range start must not exceed its end; got "
                    f"'{selector}'."
                )

        if start > maximum or end > maximum:
            raise ValueError(
                f"{option_name} selector '{selector}' is outside {range_description} "
                f"0..{maximum}."
            )

        selected.update(range(start, end + 1))

    return selected


def parse_run_index_selectors(
    selectors: list[str] | None,
    plan: experiment_plan,
) -> set[int] | None:
    """Parse global run index/range selectors against one experiment plan."""

    return _parse_index_selectors(
        selectors,
        maximum=plan.run_count - 1,
        option_name="--run-index",
        range_description="the Contract plan range",
    )


def _subject_relative_global_indices(
    plan: experiment_plan,
    selectors: list[str] | None,
    selected_subjects: set[str] | None,
) -> set[int] | None:
    if selectors is None:
        return None

    if selected_subjects is None:
        raise ValueError(
            "--subject-run-index requires at least one --subject selection."
        )

    selected_global_indices: set[int] = set()

    for subject_id in selected_subjects:
        subject_runs = tuple(
            run for run in plan.runs if run.subject_id == subject_id
        )
        relative_indices = _parse_index_selectors(
            selectors,
            maximum=len(subject_runs) - 1,
            option_name="--subject-run-index",
            range_description=f"subject '{subject_id}' run range",
        )

        assert relative_indices is not None
        selected_global_indices.update(
            subject_runs[relative_index].run_index
            for relative_index in relative_indices
        )

    return selected_global_indices


def _parse_repetition_selectors(
    selectors: list[str] | None,
    *,
    repetitions: int,
) -> set[int] | None:
    if selectors is None:
        return None

    selected: set[int] = set()

    for selector in selectors:
        if selector.isdecimal():
            start = int(selector)
            end = start
        else:
            parts = selector.split("-")

            if (
                len(parts) != 2
                or not parts[0].isdecimal()
                or not parts[1].isdecimal()
            ):
                raise ValueError(
                    "--repetition must be a positive repetition number or inclusive "
                    f"range START-END; got '{selector}'."
                )

            start = int(parts[0])
            end = int(parts[1])

            if start > end:
                raise ValueError(
                    "--repetition range start must not exceed its end; got "
                    f"'{selector}'."
                )

        if start < 1 or end > repetitions:
            raise ValueError(
                f"--repetition selector '{selector}' is outside the Contract "
                f"repetition range 1..{repetitions}."
            )

        selected.update(range(start, end + 1))

    return selected


def select_planned_runs(
    plan: experiment_plan,
    contract: dict[str, Any],
    run_selectors: list[str] | None = None,
    subject_selectors: list[str] | None = None,
    subject_run_selectors: list[str] | None = None,
    scenario_selectors: list[str] | None = None,
    repetition_selectors: list[str] | None = None,
) -> tuple[planned_run, ...]:
    """Apply deterministic coordinate and semantic filters to a plan."""

    if run_selectors is not None and subject_run_selectors is not None:
        raise ValueError(
            "--run-index and --subject-run-index are mutually exclusive."
        )

    selected_subjects = set(subject_selectors) if subject_selectors else None

    if selected_subjects is not None:
        contract_subjects = tuple(subject["id"] for subject in contract["subjects"])
        unknown = sorted(selected_subjects - set(contract_subjects))

        if unknown:
            raise ValueError(
                "Unknown --subject selection(s): "
                + ", ".join(unknown)
                + ". Contract subjects: "
                + ", ".join(contract_subjects)
                + "."
            )

    selected_scenarios = set(scenario_selectors) if scenario_selectors else None

    if selected_scenarios is not None:
        contract_scenarios = tuple(scenario["id"] for scenario in contract["scenarios"])
        unknown_scenarios = sorted(selected_scenarios - set(contract_scenarios))

        if unknown_scenarios:
            raise ValueError(
                "Unknown --scenario selection(s): "
                + ", ".join(unknown_scenarios)
                + ". Contract scenarios: "
                + ", ".join(contract_scenarios)
                + "."
            )

    repetitions = contract["run_protocol"]["repetitions"]
    selected_repetitions = _parse_repetition_selectors(
        repetition_selectors,
        repetitions=repetitions,
    )

    selected_indices = parse_run_index_selectors(run_selectors, plan)
    subject_relative_indices = _subject_relative_global_indices(
        plan,
        subject_run_selectors,
        selected_subjects,
    )

    if subject_relative_indices is not None:
        selected_indices = subject_relative_indices

    selected = tuple(
        run
        for run in plan.runs
        if (selected_indices is None or run.run_index in selected_indices)
        and (selected_subjects is None or run.subject_id in selected_subjects)
        and (selected_scenarios is None or run.scenario_id in selected_scenarios)
        and (
            selected_repetitions is None
            or run.repetition in selected_repetitions
        )
    )

    if not selected:
        raise ValueError(
            "Run selection is empty after applying run, subject, scenario, and "
            "repetition filters."
        )

    return selected


def selected_subject_ids(
    selected_runs: tuple[planned_run, ...],
) -> tuple[str, ...]:
    """Return selected subject IDs in first-run encounter order."""

    ordered: list[str] = []
    seen: set[str] = set()

    for run in selected_runs:
        if run.subject_id not in seen:
            ordered.append(run.subject_id)
            seen.add(run.subject_id)

    return tuple(ordered)
