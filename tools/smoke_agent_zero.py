from __future__ import annotations

import json

from acmm.adapters.agent_zero import agent_zero_adapter
from acmm.adapters.agent_zero_api import agent_zero_runtime_api_client


def main() -> int:
    subject = {
        "id": "agent_zero_nemotron30b_smoke",
        "agent": {
            "name": "Agent Zero",
            "version": "v2.14",
        },
        "model": {
            "name": "nemotron-3-nano:30b-cloud",
            "provider": "ollama_cloud",
        },
        "configuration": {
            "context_window": 64000,
        },
    }
    scenario = {
        "id": "agent_zero_adapter_smoke",
        "task": {
            "prompt": (
                "Obtain the laboratory magic number using get_magic_number, then "
                "use add_numbers to add 13. Report the result."
            )
        },
    }
    environment = {
        "ACMM_MAGIC_NUMBER": "87",
        "ACMM_RETRIEVAL_MODE": "success",
        "ACMM_ADDITION_MODE": "success",
    }
    termination = {
        "max_steps": 32,
        "timeout_seconds": 120,
    }

    adapter = agent_zero_adapter(agent_zero_runtime_api_client())
    result = adapter.execute(subject, scenario, environment, termination)

    print(f"termination: {result.termination}")
    if result.failure is not None:
        print(f"failure: {result.failure.kind}: {result.failure.message}")
        return 1

    evidence = {item.id: item for item in result.evidence}
    print("final response:")
    print(evidence["final_response"].content.decode("utf-8"))
    print("tool calls:")
    print(json.dumps(json.loads(evidence["tool_calls"].content), indent=2))
    print("tool results:")
    print(json.dumps(json.loads(evidence["tool_results"].content), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
