from pathlib import Path

from acmm.cli import _build_parser


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "runtime_execution.yaml"
VALID_DIR = ROOT / "tests" / "fixtures" / "records" / "valid"
INVALID_DIR = ROOT / "tests" / "fixtures" / "records" / "invalid"


def test_validate_record_command_accepts_valid_record(capsys) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "validate-record",
            str(VALID_DIR / "successful_execution.yaml"),
            "--contract",
            str(CONTRACT_PATH),
        ]
    )

    assert args.handler(args) == 0

    output = capsys.readouterr().out

    assert "Run Record is valid" in output
    assert "Execution validity: valid" in output


def test_validate_record_command_accepts_invalid_execution_record(
    capsys,
) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "validate-record",
            str(VALID_DIR / "invalid_execution.yaml"),
            "--contract",
            str(CONTRACT_PATH),
        ]
    )

    assert args.handler(args) == 0

    output = capsys.readouterr().out

    assert "Run Record is valid" in output
    assert "Execution validity: invalid" in output


def test_validate_record_command_rejects_contract_hash_mismatch(
    capsys,
) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "validate-record",
            str(INVALID_DIR / "contract_hash_mismatch.yaml"),
            "--contract",
            str(CONTRACT_PATH),
        ]
    )

    assert args.handler(args) == 1

    output = capsys.readouterr().out

    assert "contract_hash_mismatch" in output
    assert "Run Record validation failed" in output


def test_validate_record_command_rejects_invalid_contract(
    capsys,
) -> None:
    parser = _build_parser()
    invalid_contract = (
        ROOT
        / "tests"
        / "fixtures"
        / "contracts"
        / "invalid"
        / "unresolved_requirement.yaml"
    )

    args = parser.parse_args(
        [
            "validate-record",
            str(VALID_DIR / "successful_execution.yaml"),
            "--contract",
            str(invalid_contract),
        ]
    )

    assert args.handler(args) == 1

    output = capsys.readouterr().out

    assert "unresolved_requirement_reference" in output
    assert "Run Record validation was not attempted" in output
