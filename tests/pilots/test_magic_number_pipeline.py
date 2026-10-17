from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

from acmm.contract.loader import load_contract
from acmm.pilots.magic_number import build_magic_number_hermes_registry
from acmm.record.validator import (
    load_run_record_schema,
    sha256_file,
    validate_run_record,
)
from acmm.runtime.execution import execute_planned_run
from acmm.runtime.planner import build_experiment_plan


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


def _stream() -> str:
    events = [
        {
            "type": "system",
            "subtype": "init",
            "timestamp": 1,
            "model": "nemotron-3-nano:30b-cloud",
            "session_id": "pilot-session",
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "name": "get_magic_number",
            "input": {},
        },
        {
            "type": "tool_result",
            "timestamp": 3,
            "name": "get_magic_number",
            "output": json.dumps({"magic_number": 87}),
            "duration_ms": 1,
            "is_error": False,
        },
        {
            "type": "tool_use",
            "timestamp": 4,
            "name": "add_numbers",
            "input": {
                "a": 87,
                "b": 13,
            },
        },
        {
            "type": "tool_result",
            "timestamp": 5,
            "name": "add_numbers",
            "output": json.dumps({"result": 100}),
            "duration_ms": 1,
            "is_error": False,
        },
        {
            "type": "result",
            "timestamp": 6,
            "session_id": "pilot-session",
            "exit_code": 0,
            "text": "The laboratory magic number is 87, and 87 + 13 = 100.",
        },
    ]

    return "\n".join(json.dumps(event) for event in events) + "\n"


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

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=_stream(),
            stderr="",
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
        registry=build_magic_number_hermes_registry(),
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

    schema = load_run_record_schema(RECORD_SCHEMA_PATH)
    validation = validate_run_record(
        result.record,
        schema,
        contract,
        expected_contract_sha256=digest,
    )

    assert validation.is_valid, validation.errors
