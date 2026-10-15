from pathlib import Path

import pytest

from acmm.contract.loader import load_contract
from acmm.record.loader import load_run_record
from acmm.record.validator import (
    load_run_record_schema,
    sha256_file,
    validate_run_record,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "records" / "v0_1" / "run_record.schema.json"
CONTRACT_PATH = (
    ROOT
    / "experiments"
    / "examples"
    / "magic_number"
    / "experiment.yaml"
)
VALID_DIR = ROOT / "tests" / "fixtures" / "records" / "valid"
INVALID_DIR = ROOT / "tests" / "fixtures" / "records" / "invalid"


@pytest.fixture(scope="module")
def schema() -> dict:
    return load_run_record_schema(SCHEMA_PATH)


@pytest.fixture(scope="module")
def contract() -> dict:
    return load_contract(CONTRACT_PATH)


@pytest.fixture(scope="module")
def contract_sha256() -> str:
    return sha256_file(CONTRACT_PATH)


@pytest.mark.parametrize(
    "filename",
    [
        "magic_number.yaml",
        "invalid_execution.yaml",
    ],
)
def test_valid_run_records_pass(
    schema: dict,
    contract: dict,
    contract_sha256: str,
    filename: str,
) -> None:
    record = load_run_record(VALID_DIR / filename)

    result = validate_run_record(
        record,
        schema,
        contract,
        expected_contract_sha256=contract_sha256,
    )

    assert result.is_valid
    assert result.errors == ()


@pytest.mark.parametrize(
    ("filename", "expected_code"),
    [
        ("contract_hash_mismatch.yaml", "contract_hash_mismatch"),
        ("planned_run_mismatch.yaml", "planned_run_mismatch"),
        ("missing_evidence_channel.yaml", "missing_evidence_channel"),
        ("unsafe_artifact_path.yaml", "unsafe_artifact_path"),
        ("unresolved_evidence_reference.yaml", "unresolved_evidence_reference"),
        ("missing_opportunity_judgment.yaml", "missing_opportunity_judgment"),
        ("duplicate_evidence_channel.yaml", "duplicate_evidence_channel"),
        ("duplicate_artifact_path.yaml", "duplicate_artifact_path"),
        ("invalid_without_failure.yaml", "schema_violation"),
        ("bad_time_order.yaml", "invalid_execution_time_order"),
    ],
)
def test_invalid_run_records_report_expected_error(
    schema: dict,
    contract: dict,
    contract_sha256: str,
    filename: str,
    expected_code: str,
) -> None:
    record = load_run_record(INVALID_DIR / filename)

    result = validate_run_record(
        record,
        schema,
        contract,
        expected_contract_sha256=contract_sha256,
    )

    assert not result.is_valid
    assert expected_code in {issue.code for issue in result.errors}


def test_invalid_execution_can_have_no_evidence_or_judgments(
    schema: dict,
    contract: dict,
    contract_sha256: str,
) -> None:
    record = load_run_record(VALID_DIR / "invalid_execution.yaml")

    assert record["execution"]["validity"] == "invalid"
    assert record["evidence"] == []
    assert record["judgments"] == []

    result = validate_run_record(
        record,
        schema,
        contract,
        expected_contract_sha256=contract_sha256,
    )

    assert result.is_valid
