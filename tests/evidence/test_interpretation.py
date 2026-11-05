from acmm.evidence.interpretation import interpret_scenario_evidence
from acmm.runtime.interfaces import captured_evidence


def _scenario() -> dict:
    return {
        "task": {
            "prompt": "Return configuration.",
            "output_semantics": {
                "structured_object": {
                    "evidence_id": "final_response",
                    "payload_path": "result",
                    "required_payload_keys": ["configuration"],
                },
                "scalar_fields": [
                    {
                        "path": "result.configuration.quota.value",
                        "semantic_type": "integer",
                        "accepted_serializations": ["native", "canonical_string"],
                    }
                ],
            },
        }
    }


def _evidence(text: str) -> tuple[captured_evidence, ...]:
    return (
        captured_evidence(
            id="final_response",
            channel="final_response",
            artifact="evidence/final_response.txt",
            content=text.encode("utf-8"),
        ),
    )


def test_interpretation_applies_contract_scalar_semantics_without_mutating_raw_evidence() -> None:
    raw = '{"result":{"configuration":{"quota":{"value":"6","layer":"ENV"}}}}'
    artifacts = _evidence(raw)

    views = interpret_scenario_evidence(_scenario(), artifacts)
    view = views["final_response"]

    assert artifacts[0].content.decode("utf-8") == raw
    assert view.error is None
    assert view.payload == {"configuration": {"quota": {"value": 6, "layer": "ENV"}}}
    assert view.semantic_normalizations[0].path == "result.configuration.quota.value"
    assert view.semantic_normalizations[0].source_serialization == "canonical_string"


def test_interpretation_recovers_payload_when_outer_envelope_is_damaged() -> None:
    raw = '{\\\"configuration\\\":{\\\"quota\\\":{\\\"value\\\":\\\"6\\\",\\\"layer\\\":\\\"ENV\\\"}}}'

    view = interpret_scenario_evidence(_scenario(), _evidence(raw))["final_response"]

    assert view.error is None
    assert view.payload == {"configuration": {"quota": {"value": 6, "layer": "ENV"}}}
    assert "payload_envelope_reconstruction" in view.syntax_normalizations


def test_interpretation_leaves_noncanonical_scalar_representation_unmodified() -> None:
    raw = '{"result":{"configuration":{"quota":{"value":"06","layer":"ENV"}}}}'

    view = interpret_scenario_evidence(_scenario(), _evidence(raw))["final_response"]

    assert view.error is None
    assert view.payload["configuration"]["quota"]["value"] == "06"
    assert view.semantic_normalizations == ()


def test_interpretation_tracks_exact_representation_fidelity() -> None:
    scenario = {
        "task": {
            "output_semantics": {
                "structured_object": {
                    "evidence_id": "final_response",
                    "payload_path": "result",
                    "required_payload_keys": ["count"],
                    "accepted_presentations": ["exact_json_object"],
                }
            }
        }
    }
    evidence = _evidence('{"result":{"count":6}}')

    view = interpret_scenario_evidence(scenario, evidence)["final_response"]

    assert view.representation_status == "exact_json_object"
    assert view.representation_conforming is True
    assert view.syntax_normalizations == ()


def test_interpretation_marks_recovered_json_as_nonconforming_representation() -> None:
    scenario = {
        "task": {
            "output_semantics": {
                "structured_object": {
                    "evidence_id": "final_response",
                    "payload_path": "result",
                    "required_payload_keys": ["count"],
                    "accepted_presentations": ["exact_json_object"],
                }
            }
        }
    }
    evidence = _evidence('prefix {"result":{"count":6}} suffix')

    view = interpret_scenario_evidence(scenario, evidence)["final_response"]

    assert view.payload == {"count": 6}
    assert view.representation_status == "recovered_json_object"
    assert view.representation_conforming is False
    assert view.syntax_normalizations == ("embedded_json",)
