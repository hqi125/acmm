from pathlib import Path

from acmm.cli import _build_parser


ROOT = Path(__file__).resolve().parents[2]


def test_validate_command_accepts_valid_contract() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "validate",
            str(
                ROOT
                / "tests"
                / "fixtures"
                / "contracts"
                / "valid"
                / "minimal.yaml"
            ),
        ]
    )

    assert args.handler(args) == 0


def test_validate_command_rejects_invalid_contract() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "validate",
            str(
                ROOT
                / "tests"
                / "fixtures"
                / "contracts"
                / "invalid"
                / "unresolved_requirement.yaml"
            ),
        ]
    )

    assert args.handler(args) == 1
