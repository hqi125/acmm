from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from acmm_infra.runtime_lifecycle import subject_runtime_lease


class runtime_log_capture_error(RuntimeError):
    """Raised when a requested runtime log cannot be captured."""


_ANSI_ESCAPE_RE = re.compile(
    r"""
    \x1B
    (?:
        \][^\x07\x1B]*(?:\x07|\x1B\\)
        |
        [PX^_][^\x1B]*(?:\x1B\\)
        |
        \[[0-?]*[ -/]*[@-~]
        |
        [@-_]
    )
    """,
    re.VERBOSE,
)
_OTHER_CONTROL_RE = re.compile(r"[\x00-\x07\x0B\x0C\x0E-\x1A\x1C-\x1F\x7F]")
_JSON_AUTHORIZATION_BEARER_RE = re.compile(
    r'(?i)(["\']Authorization["\']\s*:\s*["\']Bearer\s+)([^"\']+)(["\'])'
)
_AUTHORIZATION_BEARER_RE = re.compile(
    r"(?i)(Authorization\s*[:=]\s*Bearer\s+)([^\s,;}]+)"
)
_GITHUB_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9._-]+|github_pat_[A-Za-z0-9._-]+)\b"
)
_LOG_SANITIZER_ID = "acmm_plain_terminal_v2"


def capture_subject_runtime_log(
    *,
    lease: subject_runtime_lease,
    subject_id: str,
    since: datetime,
    session_dir: str | Path,
) -> Path:
    if since.tzinfo is None or since.utcoffset() is None:
        raise ValueError("Runtime log capture start time must be timezone-aware.")

    result = lease.context.manager.docker.container_logs(
        since=_format_docker_timestamp(since),
    )

    if not result.succeeded:
        detail = result.stderr.strip() or "docker logs returned a non-zero status"
        raise runtime_log_capture_error(detail)

    destination = _next_log_path(Path(session_dir), subject_id)
    destination.parent.mkdir(parents=True, exist_ok=True)

    sanitized, metadata = _sanitize_runtime_log(result.stdout)
    destination.write_text(sanitized, encoding="utf-8", newline="")
    _write_sanitization_metadata(destination, metadata)
    return destination


def _sanitize_runtime_log(text: str) -> tuple[str, dict[str, object]]:
    without_ansi, ansi_sequences_removed = _ANSI_ESCAPE_RE.subn("", text)

    crlf_pairs = without_ansi.count("\r\n")
    normalized = without_ansi.replace("\r\n", "\n")
    bare_carriage_returns = normalized.count("\r")
    normalized = normalized.replace("\r", "\n")

    normalized, backspace_edits = _apply_backspaces(normalized)
    sanitized, other_controls_removed = _OTHER_CONTROL_RE.subn("", normalized)
    sanitized, credential_values_redacted = _redact_credentials(sanitized)

    metadata: dict[str, object] = {
        "schema_version": "0.1",
        "sanitizer": _LOG_SANITIZER_ID,
        "source": "docker_logs_stdout",
        "input_utf8_bytes": len(text.encode("utf-8")),
        "output_utf8_bytes": len(sanitized.encode("utf-8")),
        "transformations": {
            "ansi_escape_sequences_removed": ansi_sequences_removed,
            "crlf_pairs_normalized": crlf_pairs,
            "bare_carriage_returns_normalized": bare_carriage_returns,
            "backspace_edits_applied": backspace_edits,
            "other_control_characters_removed": other_controls_removed,
            "credential_values_redacted": credential_values_redacted,
        },
    }
    return sanitized, metadata


def _redact_credentials(text: str) -> tuple[str, int]:
    redactions = 0

    def replace_json(match: re.Match[str]) -> str:
        nonlocal redactions
        redactions += 1
        return match.group(1) + "<redacted>" + match.group(3)

    def replace_bearer(match: re.Match[str]) -> str:
        nonlocal redactions
        redactions += 1
        return match.group(1) + "<redacted>"

    def replace_token(_match: re.Match[str]) -> str:
        nonlocal redactions
        redactions += 1
        return "<redacted>"

    redacted = _JSON_AUTHORIZATION_BEARER_RE.sub(replace_json, text)
    redacted = _AUTHORIZATION_BEARER_RE.sub(replace_bearer, redacted)
    redacted = _GITHUB_TOKEN_RE.sub(replace_token, redacted)
    return redacted, redactions


def _apply_backspaces(text: str) -> tuple[str, int]:
    output: list[str] = []
    edits = 0

    for character in text:
        if character != "\b":
            output.append(character)
            continue

        edits += 1
        if output and output[-1] != "\n":
            output.pop()

    return "".join(output), edits


def _write_sanitization_metadata(
    destination: Path,
    metadata: dict[str, object],
) -> None:
    metadata_path = destination.with_name(f"{destination.name}.meta.json")
    serialized = json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    metadata_path.write_text(serialized, encoding="utf-8", newline="")


def _next_log_path(session_dir: Path, subject_id: str) -> Path:
    _validate_directory_component("subject ID", subject_id)
    directory = session_dir / "diagnostics" / subject_id
    first = directory / "container.log"

    if not first.exists():
        return first

    segment = 2
    while True:
        candidate = directory / f"container_{segment}.log"
        if not candidate.exists():
            return candidate
        segment += 1


def _format_docker_timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _validate_directory_component(label: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string.")

    if value in {".", ".."} or "/" in value or "\\" in value or ":" in value:
        raise ValueError(
            f"{label} is not safe for a result-directory name: '{value}'."
        )
