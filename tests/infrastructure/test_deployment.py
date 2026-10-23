from pathlib import Path

import pytest

from acmm_infra.common import infrastructure_error
from acmm_infra.deployment import (
    bool_text,
    files_equal,
    install_default_if_missing,
    read_env,
    write_env,
)


def test_install_default_if_missing_creates_then_preserves(tmp_path: Path) -> None:
    source = tmp_path / "default.txt"
    destination = tmp_path / "runtime" / "default.txt"
    source.write_text("default\n", encoding="utf-8")

    assert install_default_if_missing(source, destination) == "created"
    assert destination.read_text(encoding="utf-8") == "default\n"

    destination.write_text("custom\n", encoding="utf-8")
    assert install_default_if_missing(source, destination) == "preserved"
    assert destination.read_text(encoding="utf-8") == "custom\n"


def test_install_default_if_missing_requires_source(tmp_path: Path) -> None:
    with pytest.raises(infrastructure_error):
        install_default_if_missing(
            tmp_path / "missing.txt",
            tmp_path / "runtime.txt",
        )


def test_read_env_ignores_comments_blank_lines_and_invalid_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n\nA=1\ninvalid\nB = two words\nC=value=with=equals\n",
        encoding="utf-8",
    )

    assert read_env(path) == {
        "A": "1",
        "B": "two words",
        "C": "value=with=equals",
    }
    assert read_env(tmp_path / "missing.env") == {}


def test_write_env_sorts_keys_and_honors_line_ending(tmp_path: Path) -> None:
    path = tmp_path / ".env"

    write_env(
        path,
        {"B": "2", "A": "1"},
        header_lines=("# first", "# second"),
        line_ending="\r\n",
    )

    assert path.read_bytes() == (
        b"# first\r\n"
        b"# second\r\n"
        b"\r\n"
        b"A=1\r\n"
        b"B=2\r\n"
    )


def test_files_equal_requires_equal_regular_files(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.write_bytes(b"same")
    right.write_bytes(b"same")

    assert files_equal(left, right) is True

    right.write_bytes(b"different")
    assert files_equal(left, right) is False
    assert files_equal(left, tmp_path / "missing") is False


def test_bool_text_uses_lowercase_boolean_literals() -> None:
    assert bool_text(True) == "true"
    assert bool_text(False) == "false"
