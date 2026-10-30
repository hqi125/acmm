from __future__ import annotations

from acmm.adapters.agent_zero import agent_zero_adapter
from acmm.adapters.agent_zero_api import agent_zero_runtime_api_client
from controlled_tool_smoke import (
    build_environment,
    build_scenario,
    build_subject,
    build_termination,
    print_and_check_result,
)


def main() -> int:
    adapter = agent_zero_adapter(agent_zero_runtime_api_client())
    result = adapter.execute(
        build_subject(
            subject_id="agent_zero_llm_smoke",
            agent_name="Agent Zero",
            agent_version="v2.14",
            infrastructure_subject="agent-zero",
        ),
        build_scenario(scenario_id="agent_zero_adapter_smoke"),
        build_environment(),
        build_termination(),
    )
    return print_and_check_result(result)


if __name__ == "__main__":
    raise SystemExit(main())
