from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

from acmm.contract.loader import load_contract
from acmm.pilots.magic_number import build_magic_number_registry
from acmm.record.validator import (
    load_run_record_schema,
    sha256_file,
    validate_run_record,
)
from acmm.runtime.execution import execute_planned_run
from acmm.runtime.planner import build_experiment_plan
from acmm.subjects.catalog import lookup_subject_execution_binding


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    ROOT
    / "experiments"
    / "examples"
    / "magic_number"
    / "experiment.yaml"
)
RECORD_SCHEMA_PATH = (
    ROOT
    / "records"
    / "v0_1"
    / "run_record.schema.json"
)
RUN_ID = "1c78416a-ea4a-4432-af26-c190ad45a9f6"


def _magic_number_registry():
    binding = lookup_subject_execution_binding("hermes_nemotron30b")
    assert binding is not None
    return build_magic_number_registry(
        {
            "hermes_nemotron30b": binding.adapter_builder(),
        }
    )


def _session_export() -> str:
    session = {
        "id": "pilot-session",
        "model": "nemotron-3-nano:30b-cloud",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Use the laboratory magic number and the add_numbers tool to a"
                    "dd the magic number to 13."
                ),
                "timestamp": 1,
            },
            {
                "role": "assistant",
                "content": "",
                "timestamp": 2,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "get_magic_number",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "get_magic_number",
                "content": json.dumps({"magic_number": 87}),
                "timestamp": 3,
            },
            {
                "role": "assistant",
                "content": "",
                "timestamp": 4,
                "tool_calls": [
                    {
                        "id": "call-2",
                        "type": "function",
                        "function": {
                            "name": "add_numbers",
                            "arguments": json.dumps(
                                {
                                    "a": 87,
                                    "b": 13,
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-2",
                "name": "add_numbers",
                "content": json.dumps({"result": 100}),
                "timestamp": 5,
            },
            {
                "role": "assistant",
                "content": (
                    "The laboratory magic number is 87, and 87 + 13 = 100."
                ),
                "timestamp": 6,
            },
        ],
    }

    return json.dumps(session) + "\n"


def _clock():
    current = datetime(
        2026,
        10,
        15,
        18,
        0,
        0,
        tzinfo=timezone.utc,
    )

    def now() -> datetime:
        nonlocal current
        value = current
        current += timedelta(seconds=1)
        return value

    return now


def test_magic_number_pipeline_produces_valid_compliant_run(
    monkeypatch,
) -> None:
    def runner(command, **kwargs):
        assert kwargs["env"]["ACMM_MAGIC_NUMBER"] == "87"

        if "sessions" in command and "export" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=_session_export(),
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                "The laboratory magic number is 87, and "
                "87 + 13 = 100.\n"
            ),
            stderr="session_id: pilot-session\n",
        )

    monkeypatch.setattr(
        "acmm.adapters.hermes.subprocess.run",
        runner,
    )

    contract = load_contract(CONTRACT_PATH)
    planned = build_experiment_plan(contract).runs[0]
    digest = sha256_file(CONTRACT_PATH)

    result = execute_planned_run(
        contract=contract,
        planned=planned,
        contract_sha256=digest,
        registry=_magic_number_registry(),
        harness_version="0.1.0",
        now=_clock(),
        run_id_factory=lambda: RUN_ID,
    )

    assert result.record["execution"]["validity"] == "valid"
    assert [
        judgment["outcome"]
        for judgment in result.record["judgments"]
    ] == [
        "compliant",
        "compliant",
    ]

    subject_configuration = result.record["provenance"]["subject"][
        "configuration"
    ]
    assert subject_configuration["context_window"] == 64000
    assert subject_configuration["reasoning_effort"] == "medium"
    assert subject_configuration["agent_baseline"] == "blank_slate"
    assert subject_configuration["experiment_isolation"] == {
        "title_generation_enabled": False,
        "background_review_enabled": False,
        "tool_search_enabled": "off",
    }
    assert subject_configuration["reported_model"] == (
        "nemotron-3-nano:30b-cloud"
    )
    assert subject_configuration["hermes_session_id"] == "pilot-session"

    schema = load_run_record_schema(RECORD_SCHEMA_PATH)
    validation = validate_run_record(
        result.record,
        schema,
        contract,
        expected_contract_sha256=digest,
    )

    assert validation.is_valid, validation.errors

def test_tool_search_bridge_transcript_is_adjudicated_on_real_tools(
    monkeypatch,
) -> None:
    session = {
        "id": "pilot-session",
        "model": "nemotron-3-nano:30b-cloud",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Use the laboratory magic number and the add_numbers tool "
                    "to add the magic number to 13."
                ),
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "bridge-1",
                        "type": "function",
                        "function": {
                            "name": "tool_call",
                            "arguments": json.dumps(
                                {
                                    "calls": [
                                        {
                                            "name": "get_magic_number",
                                            "arguments": {},
                                        }
                                    ]
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "bridge-1",
                "name": "tool_call",
                "content": json.dumps({"magic_number": 87}),
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "bridge-2",
                        "type": "function",
                        "function": {
                            "name": "tool_call",
                            "arguments": json.dumps(
                                {
                                    "calls": [
                                        {
                                            "name": "add_numbers",
                                            "arguments": {
                                                "a": 87,
                                                "b": 13,
                                            },
                                        }
                                    ]
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "bridge-2",
                "name": "tool_call",
                "content": json.dumps({"result": 100}),
            },
            {
                "role": "assistant",
                "content": "100 (87 + 13)",
            },
        ],
    }

    def runner(command, **kwargs):
        assert kwargs["env"]["ACMM_MAGIC_NUMBER"] == "87"

        if "sessions" in command and "export" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=json.dumps(session) + "\n",
                stderr="",
            )

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="100 (87 + 13)\n",
            stderr="session_id: pilot-session\n",
        )

    monkeypatch.setattr(
        "acmm.adapters.hermes.subprocess.run",
        runner,
    )

    contract = load_contract(CONTRACT_PATH)
    planned = build_experiment_plan(contract).runs[0]
    digest = sha256_file(CONTRACT_PATH)

    result = execute_planned_run(
        contract=contract,
        planned=planned,
        contract_sha256=digest,
        registry=_magic_number_registry(),
        harness_version="0.1.0",
        now=_clock(),
        run_id_factory=lambda: RUN_ID,
    )

    assert result.record["execution"]["validity"] == "valid"
    assert [
        judgment["outcome"]
        for judgment in result.record["judgments"]
    ] == [
        "compliant",
        "compliant",
    ]

    tool_calls = json.loads(
        next(
            item.content
            for item in result.artifacts
            if item.id == "tool_calls"
        )
    )
    assert [item["name"] for item in tool_calls] == [
        "get_magic_number",
        "add_numbers",
    ]
    assert [item["transport_name"] for item in tool_calls] == [
        "tool_call",
        "tool_call",
    ]

