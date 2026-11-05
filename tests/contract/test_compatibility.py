from pathlib import Path

from acmm.contract.compatibility import validate_harness_compatibility
from acmm.contract.loader import load_contract
from acmm.harness.capabilities import harness_capabilities


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PATH = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "runtime_execution.yaml"


def _full_capabilities() -> harness_capabilities:
    return harness_capabilities(
        evaluators=frozenset(
            {
                "synthetic_retrieval",
                "synthetic_addition",
            }
        ),
        fixtures=frozenset({"synthetic_environment_v1"}),
        evidence_channels=frozenset(
            {
                "conversation",
                "tool_calls",
                "tool_results",
                "final_response",
            }
        ),
    )


def test_runtime_fixture_is_harness_compatible() -> None:
    contract = load_contract(EXAMPLE_PATH)

    result = validate_harness_compatibility(
        contract,
        _full_capabilities(),
    )

    assert result.is_valid
    assert result.errors == ()


def test_missing_harness_resources_are_reported() -> None:
    contract = load_contract(EXAMPLE_PATH)

    result = validate_harness_compatibility(
        contract,
        harness_capabilities(
            evaluators=frozenset(),
            fixtures=frozenset(),
            evidence_channels=frozenset(),
        ),
    )

    codes = {issue.code for issue in result.errors}

    assert "unavailable_evaluator" in codes
    assert "unavailable_fixture" in codes
    assert "unsupported_evidence_channel" in codes
