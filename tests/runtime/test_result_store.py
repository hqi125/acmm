from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
import yaml

import acmm.runtime.result_store as result_store_module
from acmm.runtime.interfaces import captured_evidence, executed_run
from acmm.runtime.result_store import persist_executed_run, result_write_error


RUN_ID = "e3f07c5d-44bc-40c8-bf8a-3c8957489fb2"


def _sample_result() -> executed_run:
    artifacts = (
        captured_evidence(
            id="tool_calls",
            channel="tool_calls",
            artifact="evidence/tool_calls.json",
            content=b'[{"name":"get_fixture_value"}]',
        ),
        captured_evidence(
            id="final_response",
            channel="final_response",
            artifact="evidence/final_response.txt",
            content=b"100",
        ),
    )

    evidence = [
        {
            "id": artifact.id,
            "channel": artifact.channel,
            "artifact": artifact.artifact,
            "sha256": hashlib.sha256(artifact.content).hexdigest(),
        }
        for artifact in artifacts
    ]

    record = {
        "schema_version": "0.1",
        "run": {
            "id": RUN_ID,
            "experiment_id": "runtime_execution_fixture_v1",
            "run_index": 0,
            "subject_id": "hermes_nemotron30b",
            "scenario_id": "magic_plus_13",
            "repetition": 1,
            "contract_sha256": "a" * 64,
        },
        "execution": {
            "started_at": "2026-10-15T14:03:21Z",
            "ended_at": "2026-10-15T14:03:25Z",
            "termination": "agent_completed",
            "validity": "valid",
        },
        "provenance": {
            "harness": {
                "version": "0.1.0",
            },
            "subject": {
                "agent": {
                    "name": "Hermes Agent",
                    "version": "0.21.3",
                },
                "model": {
                    "name": "nemotron-3-nano:30b-cloud",
                    "provider": "ollama_cloud",
                },
            },
            "environment": {
                "fixture": "synthetic_environment_v1",
            },
        },
        "evidence": evidence,
        "judgments": [
            {
                "opportunity_id": "retrieve",
                "outcome": "compliant",
                "evidence_ids": ["tool_calls"],
            },
            {
                "opportunity_id": "calculate",
                "outcome": "compliant",
                "evidence_ids": ["tool_calls", "final_response"],
            },
        ],
    }

    return executed_run(
        record=record,
        artifacts=artifacts,
    )


def _final_path(root: Path) -> Path:
    return (
        root
        / "runtime_execution_fixture_v1"
        / f"run_000000_{RUN_ID}"
    )


def test_persist_executed_run_writes_complete_bundle(tmp_path: Path) -> None:
    result = _sample_result()

    run_dir = persist_executed_run(result, tmp_path)

    assert run_dir == _final_path(tmp_path)
    assert run_dir.is_dir()

    assert (
        run_dir / "evidence" / "tool_calls.json"
    ).read_bytes() == b'[{"name":"get_fixture_value"}]'

    assert (
        run_dir / "evidence" / "final_response.txt"
    ).read_bytes() == b"100"

    persisted_record = yaml.safe_load(
        (run_dir / "run_record.yaml").read_text(encoding="utf-8")
    )

    assert persisted_record == result.record

    temporary_entries = [
        path
        for path in run_dir.parent.iterdir()
        if path.name.startswith(".run_")
    ]
    assert temporary_entries == []


def test_persist_executed_run_writes_diagnostic_artifacts(tmp_path: Path) -> None:
    result = _sample_result()
    diagnostic = captured_evidence(
        id="tool_name_repairs",
        channel="diagnostic",
        artifact="diagnostics/tool_name_repairs.json",
        content=b'{"repairs":[]}',
    )
    record = copy.deepcopy(result.record)
    record["diagnostics"] = [
        {
            "id": diagnostic.id,
            "artifact": diagnostic.artifact,
            "sha256": hashlib.sha256(diagnostic.content).hexdigest(),
        }
    ]
    run_dir = persist_executed_run(
        executed_run(record=record, artifacts=result.artifacts + (diagnostic,)),
        tmp_path,
    )

    assert (run_dir / "diagnostics" / "tool_name_repairs.json").read_bytes() == b'{"repairs":[]}'



def test_existing_result_directory_is_never_overwritten(
    tmp_path: Path,
) -> None:
    result = _sample_result()
    final_dir = _final_path(tmp_path)
    final_dir.mkdir(parents=True)
    marker = final_dir / "marker.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(result_write_error):
        persist_executed_run(result, tmp_path)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_digest_mismatch_is_rejected_before_publication(
    tmp_path: Path,
) -> None:
    result = _sample_result()
    bad_record = copy.deepcopy(result.record)
    bad_record["evidence"][0]["sha256"] = "0" * 64

    bad_result = executed_run(
        record=bad_record,
        artifacts=result.artifacts,
    )

    with pytest.raises(result_write_error, match="SHA-256"):
        persist_executed_run(bad_result, tmp_path)

    assert not _final_path(tmp_path).exists()


def test_undeclared_artifact_is_rejected(
    tmp_path: Path,
) -> None:
    result = _sample_result()

    extra = captured_evidence(
        id="extra",
        channel="conversation",
        artifact="evidence/extra.json",
        content=b"{}",
    )

    bad_result = executed_run(
        record=result.record,
        artifacts=result.artifacts + (extra,),
    )

    with pytest.raises(result_write_error, match="undeclared"):
        persist_executed_run(bad_result, tmp_path)

    assert not _final_path(tmp_path).exists()


def test_unsafe_artifact_path_is_rejected(
    tmp_path: Path,
) -> None:
    result = _sample_result()
    bad_record = copy.deepcopy(result.record)
    bad_record["evidence"][0]["artifact"] = "../tool_calls.json"

    bad_result = executed_run(
        record=bad_record,
        artifacts=(
            captured_evidence(
                id="tool_calls",
                channel="tool_calls",
                artifact="../tool_calls.json",
                content=result.artifacts[0].content,
            ),
            result.artifacts[1],
        ),
    )

    with pytest.raises(result_write_error, match="unsafe segment"):
        persist_executed_run(bad_result, tmp_path)

    assert not _final_path(tmp_path).exists()


def test_mid_write_failure_leaves_no_published_or_temp_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _sample_result()
    real_write = result_store_module._write_bytes
    write_count = 0

    def fail_on_second_write(path: Path, content: bytes) -> None:
        nonlocal write_count
        write_count += 1

        if write_count == 2:
            raise OSError("simulated write failure")

        real_write(path, content)

    monkeypatch.setattr(
        result_store_module,
        "_write_bytes",
        fail_on_second_write,
    )

    with pytest.raises(result_write_error, match="simulated write failure"):
        persist_executed_run(result, tmp_path)

    experiment_dir = tmp_path / "runtime_execution_fixture_v1"

    assert not _final_path(tmp_path).exists()
    assert list(experiment_dir.iterdir()) == []

def test_unsafe_experiment_id_cannot_escape_results_root(
    tmp_path: Path,
) -> None:
    result = _sample_result()
    bad_record = copy.deepcopy(result.record)
    bad_record["run"]["experiment_id"] = "../escaped"

    bad_result = executed_run(
        record=bad_record,
        artifacts=result.artifacts,
    )

    with pytest.raises(result_write_error, match="Experiment id"):
        persist_executed_run(bad_result, tmp_path)

    assert not (tmp_path.parent / "escaped").exists()



def test_keyboard_interrupt_mid_write_leaves_no_published_or_temp_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _sample_result()
    real_write = result_store_module._write_bytes
    write_count = 0

    def interrupt_on_second_write(path: Path, content: bytes) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise KeyboardInterrupt
        real_write(path, content)

    monkeypatch.setattr(result_store_module, "_write_bytes", interrupt_on_second_write)

    with pytest.raises(KeyboardInterrupt):
        persist_executed_run(result, tmp_path)

    experiment_dir = tmp_path / "runtime_execution_fixture_v1"
    assert not _final_path(tmp_path).exists()
    assert list(experiment_dir.iterdir()) == []
