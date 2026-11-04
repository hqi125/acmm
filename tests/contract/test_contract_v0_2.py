from pathlib import Path

from acmm.contract.loader import load_contract
from acmm.contract.validator import (
    contract_schema_path,
    load_schema_for_contract,
    validate_contract,
)


def _base_contract() -> dict:
    return {
        "schema_version": "0.2",
        "experiment": {"id": "semantic_output_v1"},
        "subjects": [
            {
                "id": "subject_a",
                "agent": {"name": "Agent"},
                "model": {"name": "Model"},
            }
        ],
        "requirements": [
            {
                "id": "complete_task",
                "dimension": "goal",
                "statement": "Complete the task.",
                "applicability": "The scenario is executed.",
                "compliance": "The check passes.",
            }
        ],
        "checks": [{"id": "check_result", "evaluator": "example"}],
        "scenarios": [
            {
                "id": "scenario_a",
                "task": {
                    "prompt": "Return the count.",
                    "output_semantics": {
                        "scalar_fields": [
                            {
                                "path": "result.count",
                                "semantic_type": "integer",
                                "accepted_serializations": [
                                    "native",
                                    "canonical_string",
                                ],
                            }
                        ]
                    },
                },
                "opportunities": [
                    {
                        "id": "goal",
                        "requirement_id": "complete_task",
                        "check_id": "check_result",
                    }
                ],
            }
        ],
        "evidence_policy": {"capture": ["final_response"]},
        "run_protocol": {
            "repetitions": 1,
            "reset": {
                "conversation": "fresh",
                "memory": "baseline",
                "workspace": "baseline",
                "environment": "baseline",
            },
            "termination": {"max_steps": 1},
        },
    }


def test_contract_v0_2_accepts_explicit_scalar_output_semantics() -> None:
    contract = _base_contract()
    schema = load_schema_for_contract(contract)

    result = validate_contract(contract, schema)

    assert result.is_valid
    assert not result.errors
    assert contract_schema_path("0.2").name == "contract.schema.json"


def test_contract_v0_2_rejects_duplicate_scalar_paths() -> None:
    contract = _base_contract()
    fields = contract["scenarios"][0]["task"]["output_semantics"]["scalar_fields"]
    fields.append(dict(fields[0]))
    schema = load_schema_for_contract(contract)

    result = validate_contract(contract, schema)

    assert any(issue.code == "duplicate_output_semantic_path" for issue in result.errors)


def test_contract_v0_2_rejects_redundant_string_canonical_serialization() -> None:
    contract = _base_contract()
    field = contract["scenarios"][0]["task"]["output_semantics"]["scalar_fields"][0]
    field["semantic_type"] = "string"
    schema = load_schema_for_contract(contract)

    result = validate_contract(contract, schema)

    assert any(issue.code == "redundant_string_serialization" for issue in result.errors)


def test_contract_schema_selection_preserves_v0_1() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "contracts" / "valid" / "minimal.yaml"
    contract = load_contract(fixture)
    schema = load_schema_for_contract(contract)

    result = validate_contract(contract, schema)

    assert contract["schema_version"] == "0.1"
    assert result.is_valid


def test_contract_v0_2_accepts_structured_output_binding() -> None:
    contract = _base_contract()
    output = contract["scenarios"][0]["task"]["output_semantics"]
    output["structured_object"] = {
        "evidence_id": "final_response",
        "payload_path": "result",
        "required_payload_keys": ["count"],
    }
    schema = load_schema_for_contract(contract)

    result = validate_contract(contract, schema)

    assert result.is_valid


def test_contract_v0_2_rejects_structured_binding_to_uncaptured_evidence() -> None:
    contract = _base_contract()
    output = contract["scenarios"][0]["task"]["output_semantics"]
    output["structured_object"] = {
        "evidence_id": "conversation",
        "payload_path": "result",
        "required_payload_keys": ["count"],
    }
    schema = load_schema_for_contract(contract)

    result = validate_contract(contract, schema)

    assert any(issue.code == "uncaptured_interpreted_evidence" for issue in result.errors)


def test_contract_v0_2_rejects_scalar_semantics_outside_structured_payload() -> None:
    contract = _base_contract()
    output = contract["scenarios"][0]["task"]["output_semantics"]
    output["structured_object"] = {
        "evidence_id": "final_response",
        "payload_path": "result",
        "required_payload_keys": ["count"],
    }
    output["scalar_fields"][0]["path"] = "metadata.count"
    schema = load_schema_for_contract(contract)

    result = validate_contract(contract, schema)

    assert any(issue.code == "output_semantic_path_outside_payload" for issue in result.errors)
