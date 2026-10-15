from pathlib import Path

from acmm.contract.loader import load_contract
from acmm.runtime.planner import build_experiment_plan


ROOT = Path(__file__).resolve().parents[2]
VALID_DIR = ROOT / "tests" / "fixtures" / "contracts" / "valid"
EXAMPLE_PATH = (
    ROOT
    / "experiments"
    / "examples"
    / "magic_number"
    / "experiment.yaml"
)


def test_minimal_contract_builds_one_run() -> None:
    contract = load_contract(VALID_DIR / "minimal.yaml")

    plan = build_experiment_plan(contract)

    assert plan.experiment_id == "minimal_valid"
    assert plan.run_count == 1
    assert plan.runs[0].run_index == 0
    assert plan.runs[0].subject_id == "subject_a"
    assert plan.runs[0].scenario_id == "scenario_a"
    assert plan.runs[0].repetition == 1


def test_magic_number_example_expands_repetitions() -> None:
    contract = load_contract(EXAMPLE_PATH)

    plan = build_experiment_plan(contract)

    assert plan.run_count == 5
    assert [run.run_index for run in plan.runs] == [0, 1, 2, 3, 4]
    assert [run.repetition for run in plan.runs] == [1, 2, 3, 4, 5]
    assert {run.subject_id for run in plan.runs} == {"hermes_nemotron30b"}
    assert {run.scenario_id for run in plan.runs} == {"magic_plus_13"}


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


def test_plan_is_deterministic() -> None:
    contract = load_contract(EXAMPLE_PATH)

    first = build_experiment_plan(contract)
    second = build_experiment_plan(contract)

    assert first == second
