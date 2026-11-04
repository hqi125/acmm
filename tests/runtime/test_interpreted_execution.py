from __future__ import annotations

from datetime import datetime, timezone

from acmm.runtime.execution import execute_planned_run
from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    evaluation_context,
    judgment_result,
)
from acmm.runtime.planner import build_experiment_plan
from acmm.runtime.registry import execution_registry


class _adapter:
    def execute(self, subject, scenario, environment, termination) -> adapter_execution_result:
        return adapter_execution_result(
            termination="agent_completed",
            evidence=(
                captured_evidence(
                    id="final_response",
                    channel="final_response",
                    artifact="evidence/final_response.txt",
                    content=b'prefix {"result":{"count":"6"}} suffix',
                ),
            ),
            subject_provenance={
                "agent": {"name": "Synthetic Agent"},
                "model": {"name": "Synthetic Model"},
                "configuration": {},
            },
        )


class _evaluator:
    def evaluate(self, context: evaluation_context) -> judgment_result:
        view = context.interpreted_evidence["final_response"]
        assert view.payload == {"count": 6}
        return judgment_result(
            outcome="compliant",
            evidence_ids=("final_response",),
            detail="Count is correct.",
        )


def _contract() -> dict:
    return {
        "schema_version": "0.2",
        "experiment": {"id": "interpreted_execution_v1"},
        "subjects": [
            {
                "id": "subject_a",
                "agent": {"name": "Synthetic Agent"},
                "model": {"name": "Synthetic Model"},
            }
        ],
        "requirements": [
            {
                "id": "complete",
                "dimension": "goal",
                "statement": "Complete.",
                "applicability": "Always.",
                "compliance": "Count is six.",
            }
        ],
        "checks": [{"id": "check", "evaluator": "interpreted"}],
        "scenarios": [
            {
                "id": "scenario_a",
                "task": {
                    "prompt": "Return six.",
                    "output_semantics": {
                        "structured_object": {
                            "evidence_id": "final_response",
                            "payload_path": "result",
                            "required_payload_keys": ["count"],
                        },
                        "scalar_fields": [
                            {
                                "path": "result.count",
                                "semantic_type": "integer",
                                "accepted_serializations": ["native", "canonical_string"],
                            }
                        ],
                    },
                },
                "opportunities": [
                    {
                        "id": "goal",
                        "requirement_id": "complete",
                        "check_id": "check",
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


def test_execution_binds_contract_interpreted_evidence_once_before_adjudication() -> None:
    contract = _contract()
    planned = build_experiment_plan(contract).runs[0]
    times = iter(
        (
            datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 4, 12, 0, 1, tzinfo=timezone.utc),
        )
    )

    result = execute_planned_run(
        contract=contract,
        planned=planned,
        contract_sha256="0" * 64,
        registry=execution_registry(
            subject_adapters={"subject_a": _adapter()},
            fixtures={},
            evaluators={"interpreted": _evaluator()},
        ),
        harness_version="0.1.0",
        now=lambda: next(times),
        run_id_factory=lambda: "11111111-1111-4111-8111-111111111111",
    )

    assert result.record["execution"]["validity"] == "valid"
    judgment = result.record["judgments"][0]
    assert judgment["outcome"] == "compliant"
    assert "syntax=embedded_json" in judgment["detail"]
    assert "result.count:canonical_string->integer" in judgment["detail"]
    assert result.artifacts[0].content == b'prefix {"result":{"count":"6"}} suffix'
