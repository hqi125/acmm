from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from acmm.runtime.diagnostics import (
    capture_subject_runtime_log,
    runtime_log_capture_error,
)
from acmm_infra.process import command_result


class fake_docker:
    def __init__(
        self,
        result: command_result,
    ) -> None:
        self.result = result
        self.since_values: list[str] = []

    def container_logs(
        self,
        *,
        since: str,
        timeout_seconds: float = 30.0,
    ) -> command_result:
        assert timeout_seconds == 30.0
        self.since_values.append(since)
        return self.result


def _lease(docker: fake_docker):
    return SimpleNamespace(
        context=SimpleNamespace(
            manager=SimpleNamespace(docker=docker),
        ),
    )


def test_capture_subject_runtime_log_writes_result_local_diagnostic(
    tmp_path: Path,
) -> None:
    result = command_result(
        args=("docker", "logs"),
        returncode=0,
        stdout="2026-10-25T15:00:01Z started\n",
        stderr="",
    )
    docker = fake_docker(result)
    session_dir = tmp_path / "execution_test"
    session_dir.mkdir()

    path = capture_subject_runtime_log(
        lease=_lease(docker),
        subject_id="agent_zero_nemotron30b",
        since=datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc),
        session_dir=session_dir,
    )

    assert path == (
        session_dir
        / "diagnostics"
        / "agent_zero_nemotron30b"
        / "container.log"
    )
    assert path.read_text(encoding="utf-8") == result.stdout
    assert docker.since_values == ["2026-10-25T15:00:00.000000Z"]


def test_capture_subject_runtime_log_sanitizes_terminal_presentation(
    tmp_path: Path,
) -> None:
    raw = (
        "\x1b[32mready\x1b[0m\r\n"
        "\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\\n"
        "step 1\rstep 2\n"
        "abc\bD\n"
        "\x00tail\n"
    )
    result = command_result(
        args=("docker", "logs"),
        returncode=0,
        stdout=raw,
        stderr="",
    )
    session_dir = tmp_path / "execution_test"
    session_dir.mkdir()

    path = capture_subject_runtime_log(
        lease=_lease(fake_docker(result)),
        subject_id="agent_zero_nemotron30b",
        since=datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc),
        session_dir=session_dir,
    )

    assert path.read_text(encoding="utf-8") == (
        "ready\n"
        "link\n"
        "step 1\n"
        "step 2\n"
        "abD\n"
        "tail\n"
    )

    metadata_path = path.with_name("container.log.meta.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == "0.1"
    assert metadata["sanitizer"] == "acmm_plain_terminal_v2"
    assert metadata["source"] == "docker_logs_stdout"
    assert metadata["input_utf8_bytes"] > metadata["output_utf8_bytes"]
    assert metadata["transformations"] == {
        "ansi_escape_sequences_removed": 4,
        "backspace_edits_applied": 1,
        "bare_carriage_returns_normalized": 1,
        "crlf_pairs_normalized": 1,
        "other_control_characters_removed": 1,
        "credential_values_redacted": 0,
    }


def test_capture_subject_runtime_log_redacts_bearer_and_github_tokens(
    tmp_path: Path,
) -> None:
    raw = (
        '{"headers":{"Authorization":"Bearer ghs_example.secret-token"}}\n'
        "Authorization: Bearer github_pat_example.token-value\n"
        "standalone ghp_example-token\n"
    )
    result = command_result(
        args=("docker", "logs"),
        returncode=0,
        stdout=raw,
        stderr="",
    )
    session_dir = tmp_path / "execution_test"
    session_dir.mkdir()

    path = capture_subject_runtime_log(
        lease=_lease(fake_docker(result)),
        subject_id="agent_zero_gpt_oss120b",
        since=datetime(2026, 11, 11, 21, 0, tzinfo=timezone.utc),
        session_dir=session_dir,
    )

    captured = path.read_text(encoding="utf-8")
    assert "ghs_example" not in captured
    assert "github_pat_example" not in captured
    assert "ghp_example" not in captured
    assert captured.count("<redacted>") == 3

    metadata = json.loads(
        path.with_name("container.log.meta.json").read_text(encoding="utf-8")
    )
    assert metadata["transformations"]["credential_values_redacted"] == 3


def test_capture_subject_runtime_log_preserves_multiple_runtime_leases(
    tmp_path: Path,
) -> None:
    result = command_result(
        args=("docker", "logs"),
        returncode=0,
        stdout="log\n",
        stderr="",
    )
    docker = fake_docker(result)
    session_dir = tmp_path / "execution_test"
    session_dir.mkdir()
    since = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)

    first = capture_subject_runtime_log(
        lease=_lease(docker),
        subject_id="hermes_nemotron30b",
        since=since,
        session_dir=session_dir,
    )
    second = capture_subject_runtime_log(
        lease=_lease(docker),
        subject_id="hermes_nemotron30b",
        since=since,
        session_dir=session_dir,
    )

    assert first.name == "container.log"
    assert second.name == "container_2.log"


def test_capture_subject_runtime_log_reports_docker_failure(
    tmp_path: Path,
) -> None:
    result = command_result(
        args=("docker", "logs"),
        returncode=1,
        stdout="",
        stderr="container unavailable",
    )

    with pytest.raises(runtime_log_capture_error, match="container unavailable"):
        capture_subject_runtime_log(
            lease=_lease(fake_docker(result)),
            subject_id="hermes_nemotron30b",
            since=datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc),
            session_dir=tmp_path,
        )


def test_capture_subject_runtime_log_requires_timezone_aware_start(
    tmp_path: Path,
) -> None:
    result = command_result(
        args=("docker", "logs"),
        returncode=0,
        stdout="",
        stderr="",
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        capture_subject_runtime_log(
            lease=_lease(fake_docker(result)),
            subject_id="hermes_nemotron30b",
            since=datetime(2026, 10, 25, 15, 0),
            session_dir=tmp_path,
        )
