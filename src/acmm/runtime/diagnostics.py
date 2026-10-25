from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from acmm_infra.runtime_lifecycle import subject_runtime_lease


class runtime_log_capture_error(RuntimeError):
    """Raised when a requested runtime log cannot be captured."""


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
    destination.write_text(result.stdout, encoding="utf-8", newline="")
    return destination


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
