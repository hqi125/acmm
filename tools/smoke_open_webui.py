from __future__ import annotations

from acmm.adapters.open_webui import open_webui_adapter
from acmm.adapters.open_webui_api import open_webui_runtime_api_client
from controlled_tool_smoke import (
    build_environment,
    build_scenario,
    build_subject,
    build_termination,
    print_and_check_result,
)


def main() -> int:
    adapter = open_webui_adapter(open_webui_runtime_api_client())
    result = adapter.execute(
        build_subject(
            subject_id="open_webui_nemotron30b_smoke",
            agent_name="Open WebUI",
            agent_version="0.11.4",
        ),
        build_scenario(scenario_id="open_webui_adapter_smoke"),
        build_environment(),
        build_termination(),
    )
    return print_and_check_result(result)


if __name__ == "__main__":
    raise SystemExit(main())
