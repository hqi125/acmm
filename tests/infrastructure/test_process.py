from __future__ import annotations

import sys

from acmm_infra.process import command_runner


def test_command_runner_reports_timeout() -> None:
    runner = command_runner()

    result = runner.run(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(1)",
        ],
        timeout_seconds=0.05,
    )

    assert not result.succeeded
    assert result.timed_out
    assert result.returncode == 124
    assert "timed out" in result.stderr.lower()

def test_command_runner_streams_lines_to_handler() -> None:
    runner = command_runner()
    lines: list[str] = []

    result = runner.run(
        [
            "/bin/sh",
            "-c",
            "printf 'first\\nsecond\\n'",
        ],
        line_handler=lines.append,
    )

    assert result.succeeded
    assert lines == ["first", "second"]
    assert "first" in result.stdout
    assert "second" in result.stdout

