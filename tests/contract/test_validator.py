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


def test_unused_definition_is_warning_not_error(schema: dict) -> None:
    contract = load_contract(VALID_DIR / "unused_check_warning.yaml")
    result = validate_contract(contract, schema)

    assert result.is_valid
    assert "unused_check" in {issue.code for issue in result.warnings}
