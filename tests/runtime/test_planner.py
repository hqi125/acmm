from pathlib import Path

from acmm.contract.loader import load_contract
from acmm.runtime.planner import (
    build_experiment_plan,
    build_materialization_slices,
    select_planned_runs,
)


ROOT = Path(__file__).resolve().parents[2]
VALID_DIR = ROOT / "tests" / "fixtures" / "contracts" / "valid"
EXAMPLE_PATH = VALID_DIR / "runtime_execution.yaml"


def test_minimal_contract_builds_one_run() -> None:
    contract = load_contract(VALID_DIR / "minimal.yaml")

    plan = build_experiment_plan(contract)

    assert plan.experiment_id == "minimal_valid"
    assert plan.run_count == 1
    assert plan.runs[0].run_index == 0
    assert plan.runs[0].subject_id == "subject_a"
    assert plan.runs[0].scenario_id == "scenario_a"
    assert plan.runs[0].repetition == 1


def test_runtime_fixture_expands_repetitions() -> None:
    contract = load_contract(EXAMPLE_PATH)

    plan = build_experiment_plan(contract)

    assert plan.run_count == 5
    assert [run.run_index for run in plan.runs] == [0, 1, 2, 3, 4]
    assert [run.repetition for run in plan.runs] == [1, 2, 3, 4, 5]
    assert {run.subject_id for run in plan.runs} == {"hermes_gpt_oss120b"}
    assert {run.scenario_id for run in plan.runs} == {"synthetic_plus_13"}


def test_plan_order_is_subject_then_scenario_then_repetition() -> None:
    contract = {
        "experiment": {
            "id": "ordering_test",
        },
        "subjects": [
            {"id": "subject_a"},
            {"id": "subject_b"},
        ],
        "scenarios": [
            {"id": "scenario_a"},
            {"id": "scenario_b"},
        ],
        "run_protocol": {
            "repetitions": 2,
        },
    }

    plan = build_experiment_plan(contract)

    coordinates = [
        (run.subject_id, run.scenario_id, run.repetition)
        for run in plan.runs
    ]

    assert coordinates == [
        ("subject_a", "scenario_a", 1),
        ("subject_a", "scenario_a", 2),
        ("subject_a", "scenario_b", 1),
        ("subject_a", "scenario_b", 2),
        ("subject_b", "scenario_a", 1),
        ("subject_b", "scenario_a", 2),
        ("subject_b", "scenario_b", 1),
        ("subject_b", "scenario_b", 2),
    ]


def test_plan_can_interleave_scenarios_within_repetitions() -> None:
    contract = {
        "experiment": {
            "id": "interleaved_ordering_test",
        },
        "subjects": [
            {"id": "subject_a"},
            {"id": "subject_b"},
        ],
        "scenarios": [
            {"id": "scenario_a"},
            {"id": "scenario_b"},
        ],
        "run_protocol": {
            "repetitions": 2,
            "ordering": "subject_repetition_scenario",
        },
    }

    plan = build_experiment_plan(contract)

    coordinates = [
        (run.subject_id, run.scenario_id, run.repetition)
        for run in plan.runs
    ]

    assert coordinates == [
        ("subject_a", "scenario_a", 1),
        ("subject_a", "scenario_b", 1),
        ("subject_a", "scenario_a", 2),
        ("subject_a", "scenario_b", 2),
        ("subject_b", "scenario_a", 1),
        ("subject_b", "scenario_b", 1),
        ("subject_b", "scenario_a", 2),
        ("subject_b", "scenario_b", 2),
    ]


def test_plan_is_deterministic() -> None:
    contract = load_contract(EXAMPLE_PATH)

    first = build_experiment_plan(contract)
    second = build_experiment_plan(contract)

    assert first == second

def test_plan_can_cyclically_rotate_scenarios_across_repetitions() -> None:
    contract = {
        "experiment": {
            "id": "cyclic_scenario_ordering_test",
        },
        "subjects": [
            {"id": "subject_a"},
            {"id": "subject_b"},
        ],
        "scenarios": [
            {"id": "scenario_a"},
            {"id": "scenario_b"},
            {"id": "scenario_c"},
            {"id": "scenario_d"},
        ],
        "run_protocol": {
            "repetitions": 4,
            "ordering": "subject_repetition_scenario",
            "scenario_ordering": "cyclic_rotation",
        },
    }

    plan = build_experiment_plan(contract)

    coordinates = [
        (run.subject_id, run.scenario_id, run.repetition)
        for run in plan.runs
    ]

    assert coordinates == [
        ("subject_a", "scenario_a", 1),
        ("subject_a", "scenario_b", 1),
        ("subject_a", "scenario_c", 1),
        ("subject_a", "scenario_d", 1),
        ("subject_a", "scenario_b", 2),
        ("subject_a", "scenario_c", 2),
        ("subject_a", "scenario_d", 2),
        ("subject_a", "scenario_a", 2),
        ("subject_a", "scenario_c", 3),
        ("subject_a", "scenario_d", 3),
        ("subject_a", "scenario_a", 3),
        ("subject_a", "scenario_b", 3),
        ("subject_a", "scenario_d", 4),
        ("subject_a", "scenario_a", 4),
        ("subject_a", "scenario_b", 4),
        ("subject_a", "scenario_c", 4),
        ("subject_b", "scenario_b", 1),
        ("subject_b", "scenario_c", 1),
        ("subject_b", "scenario_d", 1),
        ("subject_b", "scenario_a", 1),
        ("subject_b", "scenario_c", 2),
        ("subject_b", "scenario_d", 2),
        ("subject_b", "scenario_a", 2),
        ("subject_b", "scenario_b", 2),
        ("subject_b", "scenario_d", 3),
        ("subject_b", "scenario_a", 3),
        ("subject_b", "scenario_b", 3),
        ("subject_b", "scenario_c", 3),
        ("subject_b", "scenario_a", 4),
        ("subject_b", "scenario_b", 4),
        ("subject_b", "scenario_c", 4),
        ("subject_b", "scenario_d", 4),
    ]


def test_cyclic_scenario_ordering_resets_as_a_deterministic_subject_phase() -> None:
    contract = {
        "experiment": {
            "id": "cyclic_partial_cycle_test",
        },
        "subjects": [
            {"id": "subject_a"},
            {"id": "subject_b"},
            {"id": "subject_c"},
        ],
        "scenarios": [
            {"id": "scenario_a"},
            {"id": "scenario_b"},
            {"id": "scenario_c"},
            {"id": "scenario_d"},
        ],
        "run_protocol": {
            "repetitions": 1,
            "ordering": "subject_repetition_scenario",
            "scenario_ordering": "cyclic_rotation",
        },
    }

    plan = build_experiment_plan(contract)

    starts = [
        run.scenario_id
        for run in plan.runs
        if run.run_index % 4 == 0
    ]

    assert starts == ["scenario_a", "scenario_b", "scenario_c"]



def test_run_selection_intersects_ranges_and_subjects() -> None:
    contract = load_contract(VALID_DIR / "package_smoke.yaml")
    plan = build_experiment_plan(contract)

    selected = select_planned_runs(
        plan,
        contract,
        ["1-4", "2-5"],
        ["open_webui_gpt_oss120b"],
    )

    assert [run.run_index for run in selected] == [2, 3]
    assert {run.subject_id for run in selected} == {"open_webui_gpt_oss120b"}


def test_subject_relative_run_selection_uses_each_subject_subsequence() -> None:
    contract = load_contract(VALID_DIR / "package_smoke.yaml")
    plan = build_experiment_plan(contract)

    selected = select_planned_runs(
        plan,
        contract,
        subject_selectors=[
            "open_webui_gpt_oss120b",
            "agent_zero_gpt_oss120b",
        ],
        subject_run_selectors=["0"],
    )

    assert [run.run_index for run in selected] == [2, 4]
    assert [run.repetition for run in selected] == [1, 1]


def test_subject_relative_run_selection_requires_subject() -> None:
    contract = load_contract(VALID_DIR / "package_smoke.yaml")
    plan = build_experiment_plan(contract)

    try:
        select_planned_runs(
            plan,
            contract,
            subject_run_selectors=["0"],
        )
    except ValueError as exc:
        assert "requires at least one --subject" in str(exc)
    else:
        raise AssertionError("subject-relative selection must require --subject")


def test_semantic_scenario_and_repetition_selection_ignores_rotation_position() -> None:
    contract = {
        "experiment": {
            "id": "semantic_selection_test",
        },
        "subjects": [
            {"id": "subject_a"},
            {"id": "subject_b"},
        ],
        "scenarios": [
            {"id": "scenario_a"},
            {"id": "scenario_b"},
            {"id": "scenario_c"},
        ],
        "run_protocol": {
            "repetitions": 2,
            "ordering": "subject_repetition_scenario",
            "scenario_ordering": "cyclic_rotation",
        },
    }
    plan = build_experiment_plan(contract)

    selected = select_planned_runs(
        plan,
        contract,
        scenario_selectors=["scenario_a"],
        repetition_selectors=["1"],
    )

    assert [
        (run.subject_id, run.scenario_id, run.repetition)
        for run in selected
    ] == [
        ("subject_a", "scenario_a", 1),
        ("subject_b", "scenario_a", 1),
    ]
    assert [run.run_index for run in selected] == [0, 8]


def test_semantic_selection_combines_with_subject_filter() -> None:
    contract = {
        "experiment": {
            "id": "semantic_subject_selection_test",
        },
        "subjects": [
            {"id": "subject_a"},
            {"id": "subject_b"},
        ],
        "scenarios": [
            {"id": "scenario_a"},
            {"id": "scenario_b"},
        ],
        "run_protocol": {
            "repetitions": 3,
            "ordering": "subject_repetition_scenario",
            "scenario_ordering": "cyclic_rotation",
        },
    }
    plan = build_experiment_plan(contract)

    selected = select_planned_runs(
        plan,
        contract,
        subject_selectors=["subject_b"],
        scenario_selectors=["scenario_a"],
        repetition_selectors=["2-3"],
    )

    assert [
        (run.subject_id, run.scenario_id, run.repetition)
        for run in selected
    ] == [
        ("subject_b", "scenario_a", 2),
        ("subject_b", "scenario_a", 3),
    ]


def test_repetition_selection_is_one_based_and_bounded() -> None:
    contract = load_contract(VALID_DIR / "package_smoke.yaml")
    plan = build_experiment_plan(contract)

    for selector in ("0", "3"):
        try:
            select_planned_runs(
                plan,
                contract,
                repetition_selectors=[selector],
            )
        except ValueError as exc:
            assert "repetition range 1..2" in str(exc)
        else:
            raise AssertionError("out-of-range repetition must fail")


def test_materialization_slices_define_outer_order_and_subject_major_inner_order() -> None:
    contract = {
        "experiment": {"id": "materialization_order_test"},
        "subjects": [
            {"id": "subject_a"},
            {"id": "subject_b"},
        ],
        "scenarios": [
            {"id": "scenario_a"},
            {"id": "scenario_b"},
        ],
        "run_protocol": {
            "repetitions": 2,
            "ordering": "subject_repetition_scenario",
            "scenario_ordering": "cyclic_rotation",
        },
    }
    plan = build_experiment_plan(contract)

    slices = build_materialization_slices(
        plan.runs,
        subject_order=("subject_a", "subject_b"),
        group_by=("scenario_id",),
    )

    assert [item.key for item in slices] == [
        "scenario_id=scenario_a",
        "scenario_id=scenario_b",
    ]
    assert [
        (run.subject_id, run.scenario_id, run.repetition)
        for run in slices[0].runs
    ] == [
        ("subject_a", "scenario_a", 1),
        ("subject_a", "scenario_a", 2),
        ("subject_b", "scenario_a", 1),
        ("subject_b", "scenario_a", 2),
    ]
    assert [
        (run.subject_id, run.scenario_id, run.repetition)
        for run in slices[1].runs
    ] == [
        ("subject_a", "scenario_b", 1),
        ("subject_a", "scenario_b", 2),
        ("subject_b", "scenario_b", 1),
        ("subject_b", "scenario_b", 2),
    ]
