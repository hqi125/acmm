from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
from typing import Any, Sequence
import uuid


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SESSION_PATTERN = re.compile(r"^\s*session:\s+(.+?)\s*$", re.MULTILINE)


class cold_start_qualification_error(RuntimeError):
    """Raised when the harness cannot preserve a controlled cold-start boundary."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _command_text(command: Sequence[str]) -> str:
    return shlex.join(str(item) for item in command)


def _append_transcript(path: Path, heading: str, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as file:
        file.write(f"\n=== {heading} ===\n")
        if text:
            file.write(text)
            if not text.endswith("\n"):
                file.write("\n")


def _run_json_command(
    command: Sequence[str],
    *,
    cwd: Path,
    transcript_path: Path,
) -> tuple[int, dict[str, Any] | None, str]:
    started = time.monotonic()
    try:
        process = subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise cold_start_qualification_error(
            f"Could not execute '{command[0]}': {exc}"
        ) from exc

    duration = time.monotonic() - started
    combined = process.stdout
    if process.stderr:
        combined += process.stderr

    _append_transcript(
        transcript_path,
        f"command ({duration:.3f}s): {_command_text(command)}",
        combined,
    )

    payload: dict[str, Any] | None = None
    if process.returncode == 0:
        try:
            parsed = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise cold_start_qualification_error(
                f"Command returned invalid JSON: {_command_text(command)}: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise cold_start_qualification_error(
                f"Command returned non-object JSON: {_command_text(command)}"
            )
        payload = parsed

    return process.returncode, payload, combined


def _run_streaming_command(
    command: Sequence[str],
    *,
    cwd: Path,
    transcript_path: Path,
) -> tuple[int, str, float]:
    started = time.monotonic()
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    captured: list[str] = []

    with transcript_path.open("a", encoding="utf-8", newline="") as transcript:
        heading = f"\n=== command: {_command_text(command)} ===\n"
        transcript.write(heading)
        transcript.flush()

        try:
            process = subprocess.Popen(
                list(command),
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise cold_start_qualification_error(
                f"Could not execute '{command[0]}': {exc}"
            ) from exc

        assert process.stdout is not None
        try:
            for line in process.stdout:
                captured.append(line)
                transcript.write(line)
                transcript.flush()
                print(line, end="", flush=True)
            return_code = process.wait()
        except KeyboardInterrupt:
            process.send_signal(signal.SIGINT)
            try:
                return_code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    return_code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    return_code = process.wait()
            raise

    return return_code, "".join(captured), time.monotonic() - started


def _subject_running(status: dict[str, Any], subject_id: str) -> bool:
    subjects = status.get("subjects")
    if not isinstance(subjects, list):
        raise cold_start_qualification_error(
            "Environment status JSON does not define a 'subjects' list."
        )

    for item in subjects:
        if isinstance(item, dict) and item.get("subject_id") == subject_id:
            running = item.get("running")
            if not isinstance(running, bool):
                raise cold_start_qualification_error(
                    f"Environment status for '{subject_id}' has no boolean running state."
                )
            return running

    raise cold_start_qualification_error(
        f"Environment status did not include subject '{subject_id}'."
    )


def _extract_session(output: str) -> str | None:
    match = _SESSION_PATTERN.search(output)
    if match is None:
        return None
    return match.group(1)


def _write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    temp_path.replace(path)


def _build_stop_command(
    acmm_command: str,
    subject_id: str,
    repo_root: Path,
    runtime_root: Path,
) -> list[str]:
    return [
        acmm_command,
        "env",
        "stop",
        "--subject",
        subject_id,
        "--repo-root",
        str(repo_root),
        "--runtime-root",
        str(runtime_root),
        "--json",
    ]


def _build_status_command(
    acmm_command: str,
    subject_id: str,
    repo_root: Path,
    runtime_root: Path,
) -> list[str]:
    return [
        acmm_command,
        "env",
        "status",
        "--subject",
        subject_id,
        "--repo-root",
        str(repo_root),
        "--runtime-root",
        str(runtime_root),
        "--json",
    ]


def _build_preview_command(
    *,
    acmm_command: str,
    contract: Path,
    subject_id: str,
    run_indices: Sequence[str],
    repo_root: Path,
    runtime_root: Path,
) -> list[str]:
    command = [
        acmm_command,
        "run",
        str(contract),
        "--subject",
        subject_id,
        "--preview",
        "--repo-root",
        str(repo_root),
        "--runtime-root",
        str(runtime_root),
    ]
    for selector in run_indices:
        command.extend(("--run-index", selector))
    return command


def _build_run_command(
    *,
    acmm_command: str,
    contract: Path,
    subject_id: str,
    run_indices: Sequence[str],
    repo_root: Path,
    runtime_root: Path,
    results_root: Path,
    startup_timeout_seconds: float,
    capture_runtime_logs: bool,
) -> list[str]:
    command = [
        acmm_command,
        "run",
        str(contract),
        "--subject",
        subject_id,
        "--start-if-needed",
        "--startup-timeout-seconds",
        str(startup_timeout_seconds),
        "--repo-root",
        str(repo_root),
        "--runtime-root",
        str(runtime_root),
        "--results-root",
        str(results_root),
    ]
    for selector in run_indices:
        command.extend(("--run-index", selector))
    if capture_runtime_logs:
        command.append("--capture-runtime-logs")
    return command


def run_qualification(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).expanduser().resolve()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    results_root = Path(args.results_root).expanduser()
    if not results_root.is_absolute():
        results_root = (repo_root / results_root).resolve()
    else:
        results_root = results_root.resolve()

    contract = Path(args.contract).expanduser()
    if not contract.is_absolute():
        contract = (repo_root / contract).resolve()
    else:
        contract = contract.resolve()

    if not contract.is_file():
        raise cold_start_qualification_error(
            f"Contract does not exist: '{contract}'."
        )
    if args.iterations < 1:
        raise cold_start_qualification_error("--iterations must be at least 1.")
    if args.startup_timeout_seconds <= 0:
        raise cold_start_qualification_error(
            "--startup-timeout-seconds must be greater than zero."
        )

    qualification_id = str(uuid.uuid4())
    artifact_dir = (
        results_root
        / "_cold_start_qualifications"
        / f"qualification_{qualification_id}"
    )
    summary_path = artifact_dir / "qualification_summary.json"
    started_utc = _utc_now()

    summary: dict[str, Any] = {
        "schema_version": "1",
        "qualification": {
            "id": qualification_id,
            "started_utc": started_utc,
            "ended_utc": None,
            "subject_id": args.subject,
            "contract": str(contract),
            "iterations_requested": args.iterations,
            "iterations_completed": 0,
            "run_indices": list(args.run_index),
            "capture_runtime_logs": args.capture_runtime_logs,
            "startup_timeout_seconds": args.startup_timeout_seconds,
            "repo_root": str(repo_root),
            "runtime_root": str(runtime_root),
            "results_root": str(results_root),
        },
        "iterations": [],
    }
    _write_summary(summary_path, summary)

    stop_command = _build_stop_command(
        args.acmm_command,
        args.subject,
        repo_root,
        runtime_root,
    )
    status_command = _build_status_command(
        args.acmm_command,
        args.subject,
        repo_root,
        runtime_root,
    )
    preview_command = _build_preview_command(
        acmm_command=args.acmm_command,
        contract=contract,
        subject_id=args.subject,
        run_indices=args.run_index,
        repo_root=repo_root,
        runtime_root=runtime_root,
    )
    preview_transcript = artifact_dir / "preview.log"
    preview_code, _, _ = _run_streaming_command(
        preview_command,
        cwd=repo_root,
        transcript_path=preview_transcript,
    )
    if preview_code != 0:
        raise cold_start_qualification_error(
            "Selection preview failed; no cold-start iterations were executed. "
            f"See '{preview_transcript}'."
        )

    run_command = _build_run_command(
        acmm_command=args.acmm_command,
        contract=contract,
        subject_id=args.subject,
        run_indices=args.run_index,
        repo_root=repo_root,
        runtime_root=runtime_root,
        results_root=results_root,
        startup_timeout_seconds=args.startup_timeout_seconds,
        capture_runtime_logs=args.capture_runtime_logs,
    )

    print("ACMM cold-start qualification:")
    print(f"  subject:      {args.subject}")
    print(f"  contract:     {contract}")
    print(f"  iterations:   {args.iterations}")
    print(f"  run indices:  {', '.join(args.run_index)}")
    print(f"  artifacts:    {artifact_dir}")
    print()

    exit_code = 0
    interrupted = False

    try:
        for iteration_number in range(1, args.iterations + 1):
            iteration_started_utc = _utc_now()
            iteration_started = time.monotonic()
            transcript_path = artifact_dir / f"iteration_{iteration_number:03d}.log"
            iteration: dict[str, Any] = {
                "iteration": iteration_number,
                "started_utc": iteration_started_utc,
                "ended_utc": None,
                "duration_seconds": None,
                "cold_start_established": False,
                "run_exit_code": None,
                "execution_session": None,
                "runtime_stopped_after_run": None,
                "cleanup_forced": False,
                "transcript": str(transcript_path),
            }
            summary["iterations"].append(iteration)
            _write_summary(summary_path, summary)

            print(f"=== cold start {iteration_number}/{args.iterations} ===")
            stop_code, _, _ = _run_json_command(
                stop_command,
                cwd=repo_root,
                transcript_path=transcript_path,
            )
            if stop_code != 0:
                raise cold_start_qualification_error(
                    f"Could not stop '{args.subject}' before iteration "
                    f"{iteration_number}; cold-start control was lost."
                )

            status_code, status, _ = _run_json_command(
                status_command,
                cwd=repo_root,
                transcript_path=transcript_path,
            )
            if status_code != 0 or status is None:
                raise cold_start_qualification_error(
                    f"Could not verify stopped state before iteration {iteration_number}."
                )
            if _subject_running(status, args.subject):
                raise cold_start_qualification_error(
                    f"Subject '{args.subject}' remained running before iteration "
                    f"{iteration_number}."
                )

            iteration["cold_start_established"] = True
            _write_summary(summary_path, summary)

            print("  boundary: subject stopped; starting selected run set")
            run_code, run_output, _ = _run_streaming_command(
                run_command,
                cwd=repo_root,
                transcript_path=transcript_path,
            )
            iteration["run_exit_code"] = run_code
            iteration["execution_session"] = _extract_session(run_output)
            if run_code != 0:
                exit_code = 1
                print(
                    f"  observation: acmm run exited {run_code}; preserving as "
                    "qualification evidence",
                    file=sys.stderr,
                )

            status_code, status, _ = _run_json_command(
                status_command,
                cwd=repo_root,
                transcript_path=transcript_path,
            )
            if status_code != 0 or status is None:
                raise cold_start_qualification_error(
                    f"Could not verify runtime state after iteration {iteration_number}."
                )

            stopped_after_run = not _subject_running(status, args.subject)
            iteration["runtime_stopped_after_run"] = stopped_after_run
            if not stopped_after_run:
                iteration["cleanup_forced"] = True
                print("  cleanup: runtime still running; forcing stop")
                cleanup_code, _, _ = _run_json_command(
                    stop_command,
                    cwd=repo_root,
                    transcript_path=transcript_path,
                )
                if cleanup_code != 0:
                    raise cold_start_qualification_error(
                        f"Could not restore stopped state after iteration "
                        f"{iteration_number}."
                    )
                verify_code, verify_status, _ = _run_json_command(
                    status_command,
                    cwd=repo_root,
                    transcript_path=transcript_path,
                )
                if (
                    verify_code != 0
                    or verify_status is None
                    or _subject_running(verify_status, args.subject)
                ):
                    raise cold_start_qualification_error(
                        f"Subject '{args.subject}' could not be verified stopped "
                        f"after iteration {iteration_number}."
                    )

            iteration["ended_utc"] = _utc_now()
            iteration["duration_seconds"] = round(
                time.monotonic() - iteration_started,
                6,
            )
            summary["qualification"]["iterations_completed"] = iteration_number
            _write_summary(summary_path, summary)
            print(
                f"  complete: run exit={run_code}, "
                f"session={iteration['execution_session'] or 'none'}"
            )
            print()
    except KeyboardInterrupt:
        interrupted = True
        exit_code = 130
        print("\nCold-start qualification interrupted.", file=sys.stderr)
        try:
            cleanup_transcript = artifact_dir / "interrupt_cleanup.log"
            _run_json_command(
                stop_command,
                cwd=repo_root,
                transcript_path=cleanup_transcript,
            )
        except cold_start_qualification_error as exc:
            print(f"Cleanup warning: {exc}", file=sys.stderr)
    except cold_start_qualification_error:
        exit_code = 2
        raise
    finally:
        summary["qualification"]["ended_utc"] = _utc_now()
        summary["qualification"]["interrupted"] = interrupted
        summary["qualification"]["exit_code"] = exit_code
        _write_summary(summary_path, summary)

    print("Cold-start qualification complete:")
    print(
        "  iterations: "
        f"{summary['qualification']['iterations_completed']}/{args.iterations}"
    )
    print(f"  summary:    {summary_path}")
    return exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Repeat the same ACMM run selection across independent subject cold "
            "starts and preserve per-start diagnostic artifacts."
        )
    )
    parser.add_argument("contract", help="Path to the Experiment Contract YAML file.")
    parser.add_argument(
        "--subject",
        required=True,
        help="Exactly one Contract subject ID to cold-start qualify.",
    )
    parser.add_argument(
        "--run-index",
        action="append",
        required=True,
        metavar="INDEX|START-END",
        help=(
            "Planned-run index or inclusive range passed to acmm run. Repeat to "
            "combine selectors."
        ),
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=15,
        help="Number of independent cold starts to execute (default: 15).",
    )
    parser.add_argument(
        "--capture-runtime-logs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Capture runtime logs for each execution (default: enabled).",
    )
    parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=120.0,
        help="Startup allowance passed to acmm run (default: 120).",
    )
    parser.add_argument(
        "--repo-root",
        default=str(_REPO_ROOT),
        help="ACMM repository root (default: this repository).",
    )
    parser.add_argument(
        "--runtime-root",
        default=str(Path.home() / "acmm_runtime"),
        help="Mutable ACMM runtime root (default: ~/acmm_runtime).",
    )
    parser.add_argument(
        "--results-root",
        default="results",
        help=(
            "Execution result root passed to acmm run; qualification metadata is "
            "stored underneath it (default: results)."
        ),
    )
    parser.add_argument(
        "--acmm-command",
        default="acmm",
        help="ACMM CLI executable name/path (default: acmm).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return run_qualification(args)
    except cold_start_qualification_error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
