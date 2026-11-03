from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from acmm.runtime.interfaces import adapter_execution_result, captured_evidence, execution_failure


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SMOKE_PATH = _REPO_ROOT / "tools" / "controlled_tool_smoke.py"
_SMOKE_SPEC = importlib.util.spec_from_file_location(
    "acmm_repo_controlled_tool_smoke",
    _SMOKE_PATH,
)

assert _SMOKE_SPEC is not None
assert _SMOKE_SPEC.loader is not None

controlled_tool_smoke = importlib.util.module_from_spec(_SMOKE_SPEC)
sys.modules[_SMOKE_SPEC.name] = controlled_tool_smoke
_SMOKE_SPEC.loader.exec_module(controlled_tool_smoke)


def _evidence(
    *,
    tool_calls: list[dict[str, object]],
    tool_results: list[dict[str, object]],
    final_response: str = "The result is 100.",
) -> tuple[captured_evidence, ...]:
    return (
        captured_evidence(
            id="tool_calls",
            channel="tool_calls",
            artifact="tool_calls.json",
            content=json.dumps(tool_calls).encode("utf-8"),
        ),
        captured_evidence(
            id="tool_results",
            channel="tool_results",
            artifact="tool_results.json",
            content=json.dumps(tool_results).encode("utf-8"),
        ),
        captured_evidence(
            id="final_response",
            channel="final_response",
            artifact="final_response.txt",
            content=final_response.encode("utf-8"),
        ),
    )


def _successful_result(*, redundant_retrieval: bool = False) -> adapter_execution_result:
    tool_calls: list[dict[str, object]] = [
        {"id": "retrieve-1", "name": "get_magic_number", "input": {}},
        {"id": "add-1", "name": "add_numbers", "input": {"a": 87, "b": 13}},
    ]
    tool_results: list[dict[str, object]] = [
        {
            "id": "retrieve-1",
            "name": "get_magic_number",
            "output": '{"magic_number": 87}',
            "is_error": False,
        },
        {
            "id": "add-1",
            "name": "add_numbers",
            "output": '{"result": 100}',
            "is_error": False,
        },
    ]
    if redundant_retrieval:
        tool_calls.append({"id": "retrieve-2", "name": "get_magic_number", "input": {}})
        tool_results.append(
            {
                "id": "retrieve-2",
                "name": "get_magic_number",
                "output": '{"magic_number": 87}',
                "is_error": False,
            }
        )

    return adapter_execution_result(
        termination="agent_completed",
        evidence=_evidence(tool_calls=tool_calls, tool_results=tool_results),
        subject_provenance=None,
    )


def test_shared_fixture_matches_controlled_success_case() -> None:
    environment = controlled_tool_smoke.build_environment()
    termination = controlled_tool_smoke.build_termination()
    scenario = controlled_tool_smoke.build_scenario(scenario_id="smoke")

    generic = json.dumps(
        {
            "magic_number": 87,
            "retrieval_mode": "success",
            "addition_mode": "success",
        },
        separators=(",", ":"),
    )
    assert environment == {
        "ACMM_MAGIC_NUMBER": "87",
        "ACMM_RETRIEVAL_MODE": "success",
        "ACMM_ADDITION_MODE": "success",
        "ACMM_TOOL_CONFIG_JSON": generic,
        "ACMM_TOOL_BASELINE_JSON": generic,
    }
    assert termination == {"timeout_seconds": 120}
    assert "get_magic_number" in scenario["task"]["prompt"]
    assert "add_numbers" in scenario["task"]["prompt"]


def test_subject_builder_uses_current_llm_baseline() -> None:
    open_webui = controlled_tool_smoke.build_subject(
        subject_id="open_webui_llm_smoke",
        agent_name="Open WebUI",
        agent_version="smoke",
        infrastructure_subject="open-webui",
        repo_root=_REPO_ROOT,
    )
    agent_zero = controlled_tool_smoke.build_subject(
        subject_id="agent_zero_llm_smoke",
        agent_name="Agent Zero",
        agent_version="smoke",
        infrastructure_subject="agent-zero",
        repo_root=_REPO_ROOT,
    )

    assert open_webui["model"] == {
        "name": "gpt-oss:120b-cloud",
        "provider": "ollama_cloud",
    }
    assert open_webui["configuration"]["context_window"] == 64000
    assert open_webui["configuration"]["llm_baseline_id"] == (
        "gpt_oss_120b_cloud_v1"
    )
    assert open_webui["configuration"]["llm_requested_context_window"] is None

    assert agent_zero["configuration"]["llm_requested_context_window"] == 64000
    assert agent_zero["configuration"]["llm_utility_model"] == (
        "gpt-oss:120b-cloud"
    )
    assert agent_zero["configuration"]["llm_utility_requested_context_window"] == 64000


def test_successful_required_path_passes(capsys) -> None:
    assert controlled_tool_smoke.print_and_check_result(_successful_result()) == 0
    output = capsys.readouterr().out
    assert "required dependency path: PASS" in output
    assert "redundant controlled tool calls: 0" in output
    assert "smoke: PASS" in output


def test_redundant_successful_call_is_observation_not_failure(capsys) -> None:
    assert (
        controlled_tool_smoke.print_and_check_result(
            _successful_result(redundant_retrieval=True)
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "controlled tool calls: 3" in output
    assert "redundant controlled tool calls: 1" in output
    assert "smoke: PASS" in output


def test_missing_dependency_path_fails(capsys) -> None:
    result = adapter_execution_result(
        termination="agent_completed",
        evidence=_evidence(
            tool_calls=[
                {"id": "add-1", "name": "add_numbers", "input": {"a": 87, "b": 13}}
            ],
            tool_results=[
                {
                    "id": "add-1",
                    "name": "add_numbers",
                    "output": '{"result": 100}',
                    "is_error": False,
                }
            ],
        ),
        subject_provenance=None,
    )

    assert controlled_tool_smoke.print_and_check_result(result) == 1
    assert "required successful get_magic_number" in capsys.readouterr().out


def test_wrong_final_result_fails(capsys) -> None:
    result = _successful_result()
    result = adapter_execution_result(
        termination=result.termination,
        evidence=_evidence(
            tool_calls=json.loads(result.evidence[0].content),
            tool_results=json.loads(result.evidence[1].content),
            final_response="The task is complete.",
        ),
        subject_provenance=None,
    )

    assert controlled_tool_smoke.print_and_check_result(result) == 1
    assert "does not report expected result 100" in capsys.readouterr().out


def test_adapter_failure_fails_without_evidence_parsing(capsys) -> None:
    result = adapter_execution_result(
        termination="adapter_error",
        evidence=(),
        subject_provenance=None,
        failure=execution_failure(kind="transport", message="boom"),
    )

    assert controlled_tool_smoke.print_and_check_result(result) == 1
    output = capsys.readouterr().out
    assert "failure: transport: boom" in output
    assert "smoke: FAIL" in output
