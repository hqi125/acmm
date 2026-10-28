from pathlib import Path

import pytest

from acmm.contract.loader import load_contract
from acmm.contract.validator import load_schema, validate_contract


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "v0_1" / "contract.schema.json"
VALID_DIR = ROOT / "tests" / "fixtures" / "contracts" / "valid"
INVALID_DIR = ROOT / "tests" / "fixtures" / "contracts" / "invalid"


@pytest.fixture(scope="module")
def schema() -> dict:
    return load_schema(SCHEMA_PATH)


@pytest.mark.parametrize(
    "filename",
    [
        "minimal.yaml",
        "magic_number.yaml",
        "unused_check_warning.yaml",
    ],
)
def test_valid_contracts_pass_schema_and_semantics(
    schema: dict,
    filename: str,
) -> None:
    contract = load_contract(VALID_DIR / filename)
    result = validate_contract(contract, schema)

    assert result.is_valid


@pytest.mark.parametrize(
    ("filename", "expected_code"),
    [
        ("duplicate_requirement_id.yaml", "duplicate_id"),
        ("unresolved_requirement.yaml", "unresolved_requirement_reference"),
        ("unresolved_check.yaml", "unresolved_check_reference"),
        ("zero_repetitions.yaml", "schema_violation"),
        ("negative_inter_run_delay.yaml", "schema_violation"),
        ("unknown_run_ordering.yaml", "schema_violation"),
        ("incompatible_scenario_ordering.yaml", "incompatible_run_ordering"),
        ("unknown_field.yaml", "schema_violation"),
    ],
)
def test_invalid_contracts_report_expected_error(
    schema: dict,
    filename: str,
    expected_code: str,
) -> None:
    contract = load_contract(INVALID_DIR / filename)
    result = validate_contract(contract, schema)

    assert not result.is_valid
    assert expected_code in {issue.code for issue in result.errors}


def test_cyclic_scenario_ordering_is_valid_for_interleaved_plan(
    schema: dict,
) -> None:
    contract = load_contract(VALID_DIR / "minimal.yaml")
    contract["run_protocol"]["ordering"] = "subject_repetition_scenario"
    contract["run_protocol"]["scenario_ordering"] = "cyclic_rotation"

    result = validate_contract(contract, schema)

    assert result.is_valid


def test_unknown_scenario_ordering_is_rejected_by_schema(schema: dict) -> None:
    contract = load_contract(VALID_DIR / "minimal.yaml")
    contract["run_protocol"]["ordering"] = "subject_repetition_scenario"
    contract["run_protocol"]["scenario_ordering"] = "shuffle"

    result = validate_contract(contract, schema)

    assert not result.is_valid
    assert "schema_violation" in {issue.code for issue in result.errors}


def test_unused_definition_is_warning_not_error(schema: dict) -> None:
    contract = load_contract(VALID_DIR / "unused_check_warning.yaml")
    result = validate_contract(contract, schema)

    assert result.is_valid
    assert "unused_check" in {issue.code for issue in result.warnings}


@pytest.mark.parametrize(
    "dimension",
    [
        "goal",
        "obligation",
        "boundary",
        "procedure",
        "fidelity",
        "recovery",
    ],
)
def test_canonical_dimensions_are_valid(schema: dict, dimension: str) -> None:
    contract = load_contract(VALID_DIR / "minimal.yaml")
    contract["requirements"][0]["dimension"] = dimension

    result = validate_contract(contract, schema)

    assert result.is_valid


@pytest.mark.parametrize(
    "dimension",
    [
        "goal_fit",
        "feedback",
        "robustness",
    ],
)
def test_legacy_dimensions_are_rejected(schema: dict, dimension: str) -> None:
    contract = load_contract(VALID_DIR / "minimal.yaml")
    contract["requirements"][0]["dimension"] = dimension

    result = validate_contract(contract, schema)

    assert not result.is_valid
    assert "schema_violation" in {issue.code for issue in result.errors}
