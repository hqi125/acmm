from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from acmm.runtime.interfaces import (
    adapter_execution_result,
    captured_evidence,
    execution_failure,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SMOKE_PATH = _REPO_ROOT / "tools" / "llm_behavioral_smoke.py"
_SMOKE_SPEC = importlib.util.spec_from_file_location(
    "acmm_repo_llm_behavioral_smoke",
    _SMOKE_PATH,
)
assert _SMOKE_SPEC is not None
assert _SMOKE_SPEC.loader is not None
smoke = importlib.util.module_from_spec(_SMOKE_SPEC)
sys.modules[_SMOKE_SPEC.name] = smoke
_SMOKE_SPEC.loader.exec_module(smoke)


def _result(final_response: str = "87 + 13 = 100.") -> adapter_execution_result:
    return adapter_execution_result(
        termination="agent_completed",
        evidence=(
            captured_evidence(
                id="final_response",
                channel="final_response",
                artifact="final_response.txt",
                content=final_response.encode("utf-8"),
            ),
        ),
        subject_provenance=None,
    )


def test_shared_fixture_is_closed_world_and_tool_independent() -> None:
    assert smoke.build_environment() == {}
    assert smoke.build_termination() == {"timeout_seconds": 120}
    scenario = smoke.build_scenario(scenario_id="smoke")
    assert "87 + 13" in scenario["task"]["prompt"]
    assert "Do not use external tools" in scenario["task"]["prompt"]


def test_subject_builder_uses_current_llm_baseline() -> None:
    subject = smoke.build_subject(
        subject_id="open_webui_llm_smoke",
        agent_name="Open WebUI",
        agent_version="smoke",
        infrastructure_subject="open-webui",
        repo_root=_REPO_ROOT,
    )
    assert subject["model"] == {
        "name": "gpt-oss:120b-cloud",
        "provider": "ollama_cloud",
    }
    assert subject["configuration"]["context_window"] == 64000


def test_expected_result_passes(capsys) -> None:
    assert smoke.print_and_check_result(_result()) == 0
    output = capsys.readouterr().out
    assert "arithmetic result: PASS" in output
    assert "smoke: PASS" in output


def test_wrong_result_fails(capsys) -> None:
    assert smoke.print_and_check_result(_result("87 + 13 = 99.")) == 1
    assert "does not report expected result 100" in capsys.readouterr().out


def test_adapter_failure_fails_without_evidence_parsing(capsys) -> None:
    result = adapter_execution_result(
        termination="adapter_error",
        evidence=(),
        subject_provenance=None,
        failure=execution_failure(kind="transport", message="boom"),
    )
    assert smoke.print_and_check_result(result) == 1
    output = capsys.readouterr().out
    assert "transport: boom" in output
    assert "smoke: FAIL" in output
