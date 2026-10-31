from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any
import uuid

from acmm.adapters.agent_zero import agent_zero_adapter
from acmm.adapters.agent_zero_api import agent_zero_runtime_api_client
from acmm.adapters.hermes import hermes_adapter, hermes_adapter_config
from acmm.adapters.open_webui import open_webui_adapter
from acmm.adapters.open_webui_api import open_webui_runtime_api_client
from acmm.runtime.interfaces import adapter_execution_result, subject_adapter
from acmm_infra.cohort import inspect_cohort, resolve_deployed_cohort
from acmm_infra.common import infrastructure_error
from acmm_infra.profiles.common import default_repo_root, default_runtime_root
from acmm_infra.profiles.llm import load_llm_baseline
from acmm_infra.subject_managers import build_subject_manager


_TOOLS_ROOT = Path(__file__).resolve().parent
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))

from controlled_tool_smoke import (
    assess_result,
    build_environment,
    build_scenario,
    build_subject,
)


_SMOKE_APPARATUS_EXPERIMENT = "magic_number_tool_use_v1"
_INFRASTRUCTURE_ORDER = ("hermes", "open-webui", "agent-zero")
_AGENT_NAMES = {
    "hermes": "Hermes Agent",
    "open-webui": "Open WebUI",
    "agent-zero": "Agent Zero",
}


class llm_behavioral_qualification_error(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _build_adapter(infrastructure_subject: str) -> subject_adapter:
    if infrastructure_subject == "hermes":
        return hermes_adapter(
            hermes_adapter_config(
                docker_container="acmm-hermes",
                executable="/opt/hermes/bin/hermes",
            )
        )
    if infrastructure_subject == "open-webui":
        return open_webui_adapter(open_webui_runtime_api_client())
    if infrastructure_subject == "agent-zero":
        return agent_zero_adapter(agent_zero_runtime_api_client())
    raise llm_behavioral_qualification_error(
        f"Unsupported infrastructure subject '{infrastructure_subject}'."
    )


def _expected_agent_version(manager: Any) -> str:
    value = getattr(manager.profile, "expected_version", None)
    if not isinstance(value, str) or not value.strip():
        raise llm_behavioral_qualification_error(
            "Qualification manager does not expose an expected agent version."
        )
    return value


def _start_cold_iteration(manager: Any, *, timeout_seconds: float) -> float:
    if manager.docker.container_running():
        raise llm_behavioral_qualification_error(
            "Qualification runtime was unexpectedly running before a cold-start "
            "iteration."
        )
    started = time.monotonic()
    manager.up(timeout_seconds=timeout_seconds)
    return time.monotonic() - started


def _save_result(
    run_dir: Path,
    result: adapter_execution_result,
    assessment,
    *,
    duration_seconds: float,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for evidence in result.evidence:
        artifact = Path(evidence.artifact).name
        (run_dir / artifact).write_bytes(evidence.content)

    payload: dict[str, Any] = {
        "termination": result.termination,
        "duration_seconds": duration_seconds,
        "passed": assessment.passed,
        "detail": assessment.detail,
        "smoke_summary": (
            asdict(assessment.summary)
            if assessment.summary is not None
            else None
        ),
        "failure": (
            asdict(result.failure)
            if result.failure is not None
            else None
        ),
        "subject_provenance": result.subject_provenance,
    }
    _write_json(run_dir / "qualification_result.json", payload)


def _require_clean_deployed_baseline(repo_root: Path, runtime_root: Path):
    try:
        cohort = resolve_deployed_cohort(repo_root, runtime_root)
    except infrastructure_error as exc:
        raise llm_behavioral_qualification_error(
            "Could not resolve a clean deployed LLM baseline. The deployment may "
            "contain stale or inconsistent experiment apparatus from an interrupted "
            "qualification or earlier experiment. Run 'acmm env set-llm <llm-id>' "
            "to reconcile the intended LLM, then retry qualification. Original detail: "
            f"{exc}"
        ) from exc
    if len(cohort.subjects) != 3:
        raise llm_behavioral_qualification_error(
            "LLM behavioral qualification requires all three deployed subjects."
        )

    statuses = inspect_cohort(repo_root, runtime_root, cohort.subjects)
    errors = [item for item in statuses if item.error is not None]
    if errors:
        rendered = "; ".join(
            f"{item.subject_id}: {item.error}" for item in errors
        )
        raise llm_behavioral_qualification_error(
            f"Could not inspect the deployed qualification cohort: {rendered}"
        )

    running = [item.subject_id for item in statuses if item.running]
    if running:
        raise llm_behavioral_qualification_error(
            "Behavioral qualification requires stopped runtimes so temporary Tool "
            "apparatus can be installed safely. Stop: " + ", ".join(running)
        )

    contaminated = [
        item.subject_id
        for item in statuses
        if item.experiment_id is not None
        or item.application_scope is not None
        or item.apparatus_state != "none"
    ]
    if contaminated:
        raise llm_behavioral_qualification_error(
            "Behavioral qualification requires a clean experiment-independent "
            "baseline. Reapply the selected LLM baseline before qualification. "
            "Contaminated subjects: " + ", ".join(contaminated)
        )

    baseline_ids = {subject.llm_baseline_id for subject in cohort.subjects}
    if len(baseline_ids) != 1:
        rendered = ", ".join(sorted(baseline_ids))
        raise llm_behavioral_qualification_error(
            "Behavioral qualification requires one common deployed LLM baseline; "
            f"observed: {rendered}."
        )

    by_infrastructure = {
        subject.infrastructure_subject: subject for subject in cohort.subjects
    }
    missing = [
        item for item in _INFRASTRUCTURE_ORDER if item not in by_infrastructure
    ]
    if missing:
        raise llm_behavioral_qualification_error(
            "Behavioral qualification cohort is missing infrastructure subjects: "
            + ", ".join(missing)
        )

    baseline_id = next(iter(baseline_ids))
    return baseline_id, by_infrastructure


def _apply_smoke_tools(
    repo_root: Path,
    runtime_root: Path,
    infrastructure_subject: str,
    baseline_id: str,
    *,
    timeout_seconds: float,
):
    context = build_subject_manager(
        repo_root,
        runtime_root,
        infrastructure_subject,
        _SMOKE_APPARATUS_EXPERIMENT,
        baseline_id,
    )
    manager = context.manager

    try:
        if infrastructure_subject == "open-webui":
            manager.up(timeout_seconds=timeout_seconds)
            manager.apply_tools(
                _SMOKE_APPARATUS_EXPERIMENT,
                timeout_seconds=timeout_seconds,
            )
            return manager, True

        manager.apply_tools(
            _SMOKE_APPARATUS_EXPERIMENT,
            timeout_seconds=timeout_seconds,
        )
        manager.up(timeout_seconds=timeout_seconds)
        return manager, True
    except Exception:
        try:
            _restore_baseline_tools(
                repo_root,
                runtime_root,
                infrastructure_subject,
                baseline_id,
                manager,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            pass
        raise


def _restore_baseline_tools(
    repo_root: Path,
    runtime_root: Path,
    infrastructure_subject: str,
    baseline_id: str,
    active_manager: Any,
    *,
    timeout_seconds: float,
) -> None:
    if active_manager.docker.container_running():
        if infrastructure_subject == "open-webui":
            baseline_context = build_subject_manager(
                repo_root,
                runtime_root,
                infrastructure_subject,
                None,
                baseline_id,
            )
            baseline_context.manager.apply_tools(
                None,
                timeout_seconds=timeout_seconds,
            )
            active_manager.down()
            return
        active_manager.down()

    baseline_context = build_subject_manager(
        repo_root,
        runtime_root,
        infrastructure_subject,
        None,
        baseline_id,
    )
    baseline_manager = baseline_context.manager

    if infrastructure_subject == "open-webui":
        started_for_cleanup = False
        try:
            if not baseline_manager.docker.container_running():
                baseline_manager.up(timeout_seconds=timeout_seconds)
                started_for_cleanup = True
            baseline_manager.apply_tools(
                None,
                timeout_seconds=timeout_seconds,
            )
        finally:
            if started_for_cleanup and baseline_manager.docker.container_running():
                baseline_manager.down()
        return

    baseline_manager.apply_tools(
        None,
        timeout_seconds=timeout_seconds,
    )


def run_qualification(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).expanduser().resolve()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    results_root = Path(args.results_root).expanduser()
    if not results_root.is_absolute():
        results_root = (repo_root / results_root).resolve()
    else:
        results_root = results_root.resolve()

    if args.iterations < 1:
        raise llm_behavioral_qualification_error(
            "--iterations must be at least 1."
        )
    if args.startup_timeout_seconds <= 0:
        raise llm_behavioral_qualification_error(
            "--startup-timeout-seconds must be greater than zero."
        )
    if args.run_timeout_seconds <= 0:
        raise llm_behavioral_qualification_error(
            "--run-timeout-seconds must be greater than zero."
        )

    baseline_id, subjects = _require_clean_deployed_baseline(
        repo_root,
        runtime_root,
    )
    baseline = load_llm_baseline(repo_root, baseline_id)

    qualification_id = str(uuid.uuid4())
    output_dir = (
        results_root
        / "_llm_rebasing_qualifications"
        / f"qualification_{qualification_id}"
    )
    summary_path = output_dir / "qualification_summary.json"
    started_utc = _utc_now()
    summary: dict[str, Any] = {
        "schema_version": "1",
        "qualification": {
            "id": qualification_id,
            "kind": (
                "llm_cold_start_tool_smoke"
                if args.cold_start
                else "llm_behavioral_tool_smoke"
            ),
            "started_utc": started_utc,
            "ended_utc": None,
            "llm_baseline_id": baseline_id,
            "model": baseline.model_name,
            "provider": baseline.logical_provider,
            "iterations_per_subject": args.iterations,
            "cold_start_per_iteration": args.cold_start,
            "smoke_apparatus_source": _SMOKE_APPARATUS_EXPERIMENT,
            "repo_root": str(repo_root),
            "runtime_root": str(runtime_root),
        },
        "subjects": [],
    }
    _write_json(summary_path, summary)

    print("ACMM LLM behavioral qualification:")
    print(f"  baseline:             {baseline_id}")
    print(f"  model:                {baseline.model_name}")
    print(f"  provider:             {baseline.logical_provider}")
    print(f"  iterations/subject:   {args.iterations}")
    print(
        "  runtime mode:         "
        + ("cold start per iteration" if args.cold_start else "one warm runtime")
    )
    print(f"  engineering artifacts:{output_dir}")
    print(
        "  note:                 qualification only; no scientific experiment "
        "results are created"
    )

    overall_pass = True
    for subject_index, infrastructure_subject in enumerate(
        _INFRASTRUCTURE_ORDER,
        start=1,
    ):
        cohort_subject = subjects[infrastructure_subject]
        print()
        print(
            f"[{subject_index}/{len(_INFRASTRUCTURE_ORDER)}] "
            f"{cohort_subject.subject_id}"
        )
        print(f"  infrastructure: {infrastructure_subject}")
        print("  apparatus:       installing controlled Tool smoke fixture...")

        manager = None
        subject_record: dict[str, Any] = {
            "subject_id": cohort_subject.subject_id,
            "infrastructure_subject": infrastructure_subject,
            "iterations": [],
            "passed": False,
            "error": None,
        }
        summary["subjects"].append(subject_record)
        _write_json(summary_path, summary)

        try:
            manager, _ = _apply_smoke_tools(
                repo_root,
                runtime_root,
                infrastructure_subject,
                baseline_id,
                timeout_seconds=args.startup_timeout_seconds,
            )
            if args.cold_start:
                manager.down()
                print("  runtime:         Tool fixture prepared; stopped for cold-start cycles")
            else:
                print("  runtime:         started")

            adapter = _build_adapter(infrastructure_subject)
            subject = build_subject(
                subject_id=cohort_subject.subject_id,
                agent_name=_AGENT_NAMES[infrastructure_subject],
                agent_version=_expected_agent_version(manager),
                infrastructure_subject=infrastructure_subject,
                repo_root=repo_root,
                llm_baseline_id=baseline_id,
            )
            scenario = build_scenario(
                scenario_id="llm_rebasing_controlled_tool_smoke"
            )
            environment = build_environment()
            termination = {"timeout_seconds": args.run_timeout_seconds}

            subject_pass = True
            for iteration in range(1, args.iterations + 1):
                startup_duration: float | None = None
                if args.cold_start:
                    startup_duration = _start_cold_iteration(
                        manager,
                        timeout_seconds=args.startup_timeout_seconds,
                    )

                print(f"  run {iteration}/{args.iterations}: ", end="", flush=True)
                started = time.monotonic()
                try:
                    result = adapter.execute(
                        subject,
                        scenario,
                        environment,
                        termination,
                    )
                    duration = time.monotonic() - started
                    assessment = assess_result(result)
                    run_dir = (
                        output_dir
                        / infrastructure_subject
                        / f"iteration_{iteration:03d}"
                    )
                    _save_result(
                        run_dir,
                        result,
                        assessment,
                        duration_seconds=duration,
                    )
                    record = {
                        "iteration": iteration,
                        "cold_start": args.cold_start,
                        "startup_duration_seconds": startup_duration,
                        "passed": assessment.passed,
                        "detail": assessment.detail,
                        "termination": result.termination,
                        "duration_seconds": duration,
                        "controlled_tool_calls": (
                            assessment.summary.tool_call_count
                            if assessment.summary is not None
                            else None
                        ),
                        "redundant_tool_calls": (
                            assessment.summary.redundant_tool_call_count
                            if assessment.summary is not None
                            else None
                        ),
                        "artifact_directory": str(run_dir),
                    }
                    subject_record["iterations"].append(record)
                    _write_json(summary_path, summary)

                    if assessment.passed:
                        redundant = record["redundant_tool_calls"]
                        startup_text = (
                            f", startup={startup_duration:.2f}s"
                            if startup_duration is not None
                            else ""
                        )
                        print(
                            f"PASS ({duration:.2f}s{startup_text}, "
                            f"redundant tools={redundant})"
                        )
                    else:
                        subject_pass = False
                        overall_pass = False
                        print(f"FAIL ({duration:.2f}s): {assessment.detail}")
                finally:
                    if args.cold_start and manager.docker.container_running():
                        manager.down()

            subject_record["passed"] = subject_pass
        except Exception as exc:
            overall_pass = False
            subject_record["error"] = str(exc) or exc.__class__.__name__
            print(f"  ERROR: {subject_record['error']}")
        finally:
            if manager is not None:
                print("  cleanup:         restoring experiment-independent Tool policy...")
                try:
                    _restore_baseline_tools(
                        repo_root,
                        runtime_root,
                        infrastructure_subject,
                        baseline_id,
                        manager,
                        timeout_seconds=args.startup_timeout_seconds,
                    )
                    print("  cleanup:         restored")
                except Exception as cleanup_error:
                    overall_pass = False
                    detail = str(cleanup_error) or cleanup_error.__class__.__name__
                    prior = subject_record.get("error")
                    subject_record["error"] = (
                        f"{prior}; cleanup failed: {detail}"
                        if prior
                        else f"cleanup failed: {detail}"
                    )
                    print(f"  cleanup:         FAILED: {detail}")
            _write_json(summary_path, summary)

    summary["qualification"]["ended_utc"] = _utc_now()
    summary["qualification"]["passed"] = overall_pass
    _write_json(summary_path, summary)

    print()
    print("LLM behavioral qualification result:")
    for item in summary["subjects"]:
        completed = len(item["iterations"])
        passed = sum(1 for run in item["iterations"] if run["passed"])
        state = "PASS" if item["passed"] and item["error"] is None else "FAIL"
        print(
            f"  {item['subject_id']}: {state} "
            f"({passed}/{completed} completed runs passed)"
        )
        if item["error"] is not None:
            print(f"    error: {item['error']}")
    print(f"  summary: {summary_path}")
    print("PASS" if overall_pass else "FAIL")
    return 0 if overall_pass else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run engineering-only repeated controlled Tool qualification against "
            "the LLM baseline currently deployed across the ACMM cohort."
        )
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Controlled Tool runs per subject. Default: 5.",
    )
    parser.add_argument(
        "--cold-start",
        action="store_true",
        help=(
            "Stop after Tool-fixture preparation, then independently start and stop "
            "the subject around every controlled Tool run."
        ),
    )
    parser.add_argument(
        "--repo-root",
        default=str(default_repo_root()),
        help="ACMM repository root.",
    )
    parser.add_argument(
        "--runtime-root",
        default=str(default_runtime_root()),
        help="Mutable ACMM runtime root. Defaults to ~/acmm_runtime.",
    )
    parser.add_argument(
        "--results-root",
        default="results",
        help="Root for engineering qualification artifacts. Default: results.",
    )
    parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum startup/provisioning wait per subject. Default: 120.",
    )
    parser.add_argument(
        "--run-timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum duration of each model smoke run. Default: 120.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run_qualification(args)
    except (llm_behavioral_qualification_error, infrastructure_error, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
