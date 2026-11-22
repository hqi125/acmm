from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform
import shutil
import shlex
import subprocess
from typing import Any, Callable, Mapping, Sequence

import yaml

from acmm.analysis.availability_workbook import (
    AVAILABILITY_WORKBOOK_NAME,
    availability_workbook_error,
    build_availability_workbook,
)
from acmm.analysis.interaction_workbook import (
    INTERACTION_WORKBOOK_NAME,
    interaction_workbook_error,
    build_interaction_workbook,
)
from acmm.analysis.layout import (
    AVAILABILITY_SCOPE,
    COMPLIANCE_SCOPE,
    INTERACTION_SCOPE,
    TRAJECTORY_SCOPE,
    PROVIDER_PRESSURE_SCOPE,
    analysis_data_dir_from_root,
    analysis_log_dir_from_root,
)
from acmm.analysis.plan import (
    analysis_plan,
    analysis_plan_error,
    bootstrap_parameters,
    load_analysis_plan,
)
from acmm.analysis.workbook import (
    WORKBOOK_NAME,
    analysis_workbook_error,
    build_analysis_workbook,
)


ANALYSIS_MANIFEST_SCHEMA_VERSION = "0.1"
_PROGRESS_PREFIX = "ACMM_PROGRESS:"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_R_SCRIPT_ROOT = _REPO_ROOT / "analysis" / "r" / "scripts"


class analysis_run_error(ValueError):
    """Raised when a planned ACMM analysis cannot complete safely."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class analysis_run_result:
    analysis_id: str
    output_dir: Path
    manifest_path: Path
    source_kind: str
    population_policy: str | None
    resolved_stages: tuple[str, ...]


@dataclass(frozen=True)
class _runtime_context:
    rscript: str
    rscript_version: str
    python_version: str
    acmm_version: str
    xlsxwriter_version: str | None
    r_packages: Mapping[str, str]


@dataclass(frozen=True)
class _stage_definition:
    kind: str
    label: str
    script_name: str | None
    expected_outputs: tuple[str, ...]


_STAGE_DEFINITIONS: dict[str, _stage_definition] = {
    "requirement_estimates": _stage_definition(
        kind="requirement_estimates",
        label="requirement estimates",
        script_name="analyze_export.R",
        expected_outputs=(
            "data/compliance/requirement_estimates.csv",
            "data/compliance/population_summary.csv",
            "data/compliance/population_cell_coverage.csv",
            "data/compliance/population_missing_runs.csv",
        ),
    ),
    "requirement_uncertainty": _stage_definition(
        kind="requirement_uncertainty",
        label="hierarchical requirement uncertainty",
        script_name="analyze_uncertainty.R",
        expected_outputs=("data/compliance/requirement_intervals.csv",),
    ),
    "dimension_scores": _stage_definition(
        kind="dimension_scores",
        label="dimension scores",
        script_name="analyze_dimensions.R",
        expected_outputs=("data/compliance/dimension_scores.csv",),
    ),
    "dimension_uncertainty": _stage_definition(
        kind="dimension_uncertainty",
        label="hierarchical dimension uncertainty",
        script_name="analyze_dimension_uncertainty.R",
        expected_outputs=("data/compliance/dimension_intervals.csv",),
    ),
    "availability": _stage_definition(
        kind="availability",
        label="availability and missing-outcome sensitivity",
        script_name="analyze_availability.R",
        expected_outputs=tuple(
            f"data/availability/{name}.csv"
            for name in (
                "availability_summary",
                "availability_cells",
                "availability_positions",
                "availability_slots",
                "availability_failure_modes",
                "availability_attempt_outcomes",
                "availability_intervals",
                "availability_sensitivity",
            )
        ),
    ),
    "interaction": _stage_definition(
        kind="interaction",
        label="interaction analysis",
        script_name="analyze_interactions.R",
        expected_outputs=tuple(
            f"data/interaction/{name}.csv"
            for name in (
                "interaction_population_summary",
                "interaction_population_cell_coverage",
                "interaction_population_missing_runs",
                "interaction_cell_estimates",
                "interaction_simple_effects",
                "interaction_contrasts",
                "interaction_figures",
            )
        ),
    ),
    "trajectory": _stage_definition(
        kind="trajectory",
        label="normalized tool trajectory analysis",
        script_name="analyze_trajectory.R",
        expected_outputs=(
            "data/trajectory/trajectory_run_metrics.csv",
            "data/trajectory/trajectory_subject_summary.csv",
            "data/trajectory/trajectory_tool_usage.csv",
        ),
    ),
    "provider_pressure_summary": _stage_definition(
        kind="provider_pressure_summary",
        label="Provider Pressure trajectory summary",
        script_name="analyze_provider_pressure.R",
        expected_outputs=(
            "data/provider_pressure/provider_pressure_snapshot_summary.csv",
            "data/provider_pressure/provider_pressure_pacing_summary.csv",
            "data/provider_pressure/provider_pressure_feature_summary.csv",
            "data/provider_pressure/provider_pressure_feature_trajectory.csv",
            "data/provider_pressure/provider_pressure_admission_summary.csv",
            "data/provider_pressure/provider_pressure_utilization_summary.csv",
        ),
    ),
    "plot_run_duration": _stage_definition(
        kind="plot_run_duration",
        label="run-duration plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/operations/run_duration.png",
            "figures/operations/run_duration.pdf",
        ),
    ),
    "plot_requirement_compliance": _stage_definition(
        kind="plot_requirement_compliance",
        label="requirement compliance plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/compliance/requirement_compliance.png",
            "figures/compliance/requirement_compliance.pdf",
        ),
    ),
    "plot_dimension_scores": _stage_definition(
        kind="plot_dimension_scores",
        label="dimension score plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/compliance/dimension_scores.png",
            "figures/compliance/dimension_scores.pdf",
        ),
    ),
    "plot_dimension_profile": _stage_definition(
        kind="plot_dimension_profile",
        label="GOBPFR dimension profile plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/compliance/dimension_profile.png",
            "figures/compliance/dimension_profile.pdf",
        ),
    ),
    "plot_availability": _stage_definition(
        kind="plot_availability",
        label="availability plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/availability/availability.png",
            "figures/availability/availability.pdf",
        ),
    ),
    "plot_trajectory_run_metrics": _stage_definition(
        kind="plot_trajectory_run_metrics",
        label="trajectory run-metric plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/trajectory/trajectory_run_metrics.png",
            "figures/trajectory/trajectory_run_metrics.pdf",
        ),
    ),
    "plot_trajectory_tool_usage": _stage_definition(
        kind="plot_trajectory_tool_usage",
        label="trajectory tool-usage plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/trajectory/trajectory_tool_usage.png",
            "figures/trajectory/trajectory_tool_usage.pdf",
        ),
    ),
    "plot_trajectory_error_rate": _stage_definition(
        kind="plot_trajectory_error_rate",
        label="trajectory result-error-rate plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/trajectory/trajectory_error_rate.png",
            "figures/trajectory/trajectory_error_rate.pdf",
        ),
    ),
    "plot_trajectory_tool_composition": _stage_definition(
        kind="plot_trajectory_tool_composition",
        label="trajectory tool-composition plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/trajectory/trajectory_tool_composition.png",
            "figures/trajectory/trajectory_tool_composition.pdf",
        ),
    ),
    "plot_provider_pressure_pacing": _stage_definition(
        kind="plot_provider_pressure_pacing",
        label="Provider Pressure pacing-trajectory plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/provider_pressure/pacing_trajectory.png",
            "figures/provider_pressure/pacing_trajectory.pdf",
        ),
    ),
    "plot_provider_pressure_feature_scores": _stage_definition(
        kind="plot_provider_pressure_feature_scores",
        label="Provider Pressure feature-score plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/provider_pressure/feature_scores.png",
            "figures/provider_pressure/feature_scores.pdf",
        ),
    ),
    "plot_provider_pressure_admission_waits": _stage_definition(
        kind="plot_provider_pressure_admission_waits",
        label="Provider Pressure admission-wait plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/provider_pressure/admission_waits.png",
            "figures/provider_pressure/admission_waits.pdf",
        ),
    ),
    "plot_provider_pressure_utilization_envelope": _stage_definition(
        kind="plot_provider_pressure_utilization_envelope",
        label="Provider Pressure utilization-envelope plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/provider_pressure/utilization_envelope.png",
            "figures/provider_pressure/utilization_envelope.pdf",
        ),
    ),
    "plot_provider_pressure_utilization_by_subject": _stage_definition(
        kind="plot_provider_pressure_utilization_by_subject",
        label="Provider Pressure utilization-by-subject plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/provider_pressure/utilization_by_subject.png",
            "figures/provider_pressure/utilization_by_subject.pdf",
        ),
    ),
    "plot_provider_pressure_wait_vs_actions": _stage_definition(
        kind="plot_provider_pressure_wait_vs_actions",
        label="Provider Pressure wait-versus-actions plot",
        script_name="plot_analysis.R",
        expected_outputs=(
            "figures/provider_pressure/admission_wait_vs_actions.png",
            "figures/provider_pressure/admission_wait_vs_actions.pdf",
        ),
    ),
    "compliance_workbook": _stage_definition(
        kind="compliance_workbook",
        label="compliance workbook",
        script_name=None,
        expected_outputs=(WORKBOOK_NAME,),
    ),
    "availability_workbook": _stage_definition(
        kind="availability_workbook",
        label="availability workbook",
        script_name=None,
        expected_outputs=(AVAILABILITY_WORKBOOK_NAME,),
    ),
    "interaction_workbook": _stage_definition(
        kind="interaction_workbook",
        label="interaction workbook",
        script_name=None,
        expected_outputs=(INTERACTION_WORKBOOK_NAME,),
    ),
}


def run_analysis_plan(
    export_dir: str | Path,
    plan_path: str | Path,
    output_root: str | Path | None = None,
    plan_schema_path: str | Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> analysis_run_result:
    source_dir = Path(export_dir).expanduser().resolve()
    if not source_dir.is_dir():
        raise analysis_run_error(
            f"Analysis export directory does not exist: '{source_dir}'.",
            exit_code=2,
        )

    emit = progress or (lambda _message: None)
    emit("loading and validating the analysis plan and export binding...")

    try:
        plan = load_analysis_plan(plan_path, plan_schema_path)
    except analysis_plan_error as exc:
        raise analysis_run_error(str(exc), exit_code=2) from exc

    export_manifest_path = source_dir / "export_manifest.yaml"
    export_manifest = _load_yaml_mapping(export_manifest_path, "export manifest")
    _validate_export_binding(export_manifest)
    _validate_plan_export_compatibility(plan, export_manifest)
    export_manifest_sha256 = _sha256_file(export_manifest_path)

    emit("checking R/Python analysis runtime prerequisites...")
    runtime = _preflight_runtime(plan)
    implementation_files = _implementation_files()
    implementation_digest = _implementation_digest(implementation_files)
    referenced_inputs = _referenced_inputs(plan)
    plan_sha256 = _sha256_bytes(plan.raw_bytes)
    fingerprint = _analysis_fingerprint(
        export_manifest_sha256=export_manifest_sha256,
        plan_sha256=plan_sha256,
        implementation_digest=implementation_digest,
        referenced_inputs=referenced_inputs,
        runtime=runtime,
    )
    analysis_id = fingerprint[:16]

    analysis_root = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else source_dir / "analysis_output"
    )
    target_dir = analysis_root / f"analysis_{analysis_id}"
    staging_dir = analysis_root / f".analysis_{analysis_id}.tmp"
    failed_dir = analysis_root / f"analysis_{analysis_id}.failed"

    _prepare_output_paths(target_dir, staging_dir, failed_dir)
    staging_dir.mkdir(parents=True, exist_ok=False)

    try:
        copied_plan_path = staging_dir / "analysis_plan.yaml"
        copied_plan_path.write_bytes(plan.raw_bytes)
        copied_interaction_design = _copy_referenced_inputs(plan, staging_dir)
        total_stages = len(plan.resolved_stages)
        if plan.population_policy is None:
            emit(
                f"resolved {total_stages} stage(s) for source kind "
                f"'{plan.source_kind}'."
            )
        else:
            emit(
                f"resolved {total_stages} stage(s) under population policy "
                f"'{plan.population_policy}'."
            )

        completed_stages: list[dict[str, Any]] = []
        for index, stage_kind in enumerate(plan.resolved_stages, start=1):
            definition = _STAGE_DEFINITIONS[stage_kind]
            emit(f"[{index}/{total_stages}] running {definition.label}...")
            _execute_stage(
                stage_kind=stage_kind,
                export_dir=source_dir,
                analysis_dir=staging_dir,
                plan=plan,
                runtime=runtime,
                interaction_design=copied_interaction_design,
                progress=emit,
            )
            expected_outputs = _expected_outputs_for_stage(stage_kind, plan)
            _validate_stage_outputs(staging_dir, definition, expected_outputs)
            completed_stages.append(
                {
                    "kind": stage_kind,
                    "status": "completed",
                    "outputs": list(expected_outputs),
                }
            )
            emit(f"[{index}/{total_stages}] completed {definition.label}.")

        emit("hashing analysis artifacts and writing the provenance manifest...")
        manifest_path = staging_dir / "analysis_manifest.yaml"
        manifest = _build_analysis_manifest(
            analysis_id=analysis_id,
            fingerprint=fingerprint,
            export_manifest=export_manifest,
            export_manifest_sha256=export_manifest_sha256,
            plan=plan,
            plan_sha256=plan_sha256,
            implementation_files=implementation_files,
            implementation_digest=implementation_digest,
            referenced_inputs=referenced_inputs,
            runtime=runtime,
            completed_stages=completed_stages,
            staging_dir=staging_dir,
        )
        _write_yaml(manifest_path, manifest)
        staging_dir.replace(target_dir)
    except KeyboardInterrupt:
        _publish_failed(staging_dir, failed_dir)
        raise
    except Exception as exc:
        _publish_failed(staging_dir, failed_dir)
        if isinstance(exc, analysis_run_error):
            raise
        raise analysis_run_error(str(exc)) from exc

    emit(f"analysis published atomically to '{target_dir}'.")
    return analysis_run_result(
        analysis_id=analysis_id,
        output_dir=target_dir,
        manifest_path=target_dir / "analysis_manifest.yaml",
        source_kind=plan.source_kind,
        population_policy=plan.population_policy,
        resolved_stages=plan.resolved_stages,
    )


def _execute_stage(
    stage_kind: str,
    export_dir: Path,
    analysis_dir: Path,
    plan: analysis_plan,
    runtime: _runtime_context,
    interaction_design: Path | None,
    progress: Callable[[str], None],
) -> None:
    if stage_kind == "compliance_workbook":
        try:
            build_analysis_workbook(
                export_dir,
                analysis_dir / WORKBOOK_NAME,
                analysis_root=analysis_dir,
            )
        except analysis_workbook_error as exc:
            raise analysis_run_error(str(exc)) from exc
        return

    if stage_kind == "availability_workbook":
        try:
            build_availability_workbook(
                export_dir,
                analysis_dir / AVAILABILITY_WORKBOOK_NAME,
                analysis_root=analysis_dir,
            )
        except availability_workbook_error as exc:
            raise analysis_run_error(str(exc)) from exc
        return

    if stage_kind == "interaction_workbook":
        if interaction_design is None:
            raise analysis_run_error(
                "Interaction workbook dependency resolved without an interaction design."
            )
        try:
            build_interaction_workbook(
                export_dir,
                analysis_dir / INTERACTION_WORKBOOK_NAME,
                interaction_design,
                analysis_root=analysis_dir,
            )
        except interaction_workbook_error as exc:
            raise analysis_run_error(str(exc)) from exc
        return

    definition = _STAGE_DEFINITIONS[stage_kind]
    if definition.script_name is None:
        raise analysis_run_error(f"Analysis stage '{stage_kind}' has no implementation.")

    log_scope = _scope_for_stage(stage_kind)
    log_dir = analysis_log_dir_from_root(analysis_dir, log_scope)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{stage_kind}.txt"
    command = _stage_command(
        stage_kind,
        export_dir,
        analysis_dir,
        plan,
        runtime.rscript,
        interaction_design,
    )
    _run_r_stage(command, log_path, progress)


def _stage_command(
    stage_kind: str,
    export_dir: Path,
    analysis_dir: Path,
    plan: analysis_plan,
    rscript: str,
    interaction_design: Path | None,
) -> tuple[str, ...]:
    definition = _STAGE_DEFINITIONS[stage_kind]
    if definition.script_name is None:
        raise analysis_run_error(f"Stage '{stage_kind}' is not an R stage.")
    script = _R_SCRIPT_ROOT / definition.script_name
    population_flag = (
        f"--population-policy={plan.population_policy}"
        if plan.population_policy is not None
        else None
    )

    if stage_kind == "requirement_estimates":
        output = analysis_data_dir_from_root(
            analysis_dir,
            COMPLIANCE_SCOPE,
        ) / "requirement_estimates.csv"
        return (rscript, str(script), str(export_dir), str(output), population_flag)

    if stage_kind == "requirement_uncertainty":
        bootstrap = _bootstrap_for(plan, stage_kind)
        output = analysis_data_dir_from_root(
            analysis_dir,
            COMPLIANCE_SCOPE,
        ) / "requirement_intervals.csv"
        return (
            rscript,
            str(script),
            str(export_dir),
            str(bootstrap.replicates),
            str(bootstrap.seed),
            str(bootstrap.confidence_level),
            str(output),
            population_flag,
        )

    if stage_kind == "dimension_scores":
        output = analysis_data_dir_from_root(
            analysis_dir,
            COMPLIANCE_SCOPE,
        ) / "dimension_scores.csv"
        return (rscript, str(script), str(export_dir), str(output), population_flag)

    if stage_kind == "dimension_uncertainty":
        bootstrap = _bootstrap_for(plan, stage_kind)
        output = analysis_data_dir_from_root(
            analysis_dir,
            COMPLIANCE_SCOPE,
        ) / "dimension_intervals.csv"
        return (
            rscript,
            str(script),
            str(export_dir),
            str(bootstrap.replicates),
            str(bootstrap.seed),
            str(bootstrap.confidence_level),
            str(output),
            population_flag,
        )

    if stage_kind == "availability":
        bootstrap = _bootstrap_for(plan, stage_kind)
        return (
            rscript,
            str(script),
            str(export_dir),
            str(bootstrap.replicates),
            str(bootstrap.seed),
            str(bootstrap.confidence_level),
            str(analysis_dir),
            population_flag,
        )

    if stage_kind == "interaction":
        if interaction_design is None:
            raise analysis_run_error("Interaction analysis requires a design file.")
        bootstrap = _bootstrap_for(plan, stage_kind)
        return (
            rscript,
            str(script),
            str(export_dir),
            str(bootstrap.replicates),
            str(bootstrap.seed),
            str(bootstrap.confidence_level),
            str(analysis_dir),
            str(interaction_design),
            population_flag,
        )

    if stage_kind == "trajectory":
        assert population_flag is not None
        return (
            rscript,
            str(script),
            str(export_dir),
            str(analysis_dir),
            population_flag,
        )

    if stage_kind == "provider_pressure_summary":
        return (
            rscript,
            str(script),
            str(export_dir),
            str(analysis_dir),
        )

    plot_stage_to_kind = {
        "plot_run_duration": "run_duration",
        "plot_requirement_compliance": "requirement_compliance",
        "plot_dimension_scores": "dimension_scores",
        "plot_dimension_profile": "dimension_profile",
        "plot_availability": "availability",
        "plot_trajectory_run_metrics": "trajectory_run_metrics",
        "plot_trajectory_tool_usage": "trajectory_tool_usage",
        "plot_trajectory_error_rate": "trajectory_error_rate",
        "plot_trajectory_tool_composition": "trajectory_tool_composition",
        "plot_provider_pressure_pacing": "provider_pressure_pacing",
        "plot_provider_pressure_feature_scores": "provider_pressure_feature_scores",
        "plot_provider_pressure_admission_waits": "provider_pressure_admission_waits",
        "plot_provider_pressure_utilization_envelope": "provider_pressure_utilization_envelope",
        "plot_provider_pressure_utilization_by_subject": "provider_pressure_utilization_by_subject",
        "plot_provider_pressure_wait_vs_actions": "provider_pressure_wait_vs_actions",
    }
    plot_kind = plot_stage_to_kind.get(stage_kind)
    if plot_kind is not None:
        request = plan.plot(plot_kind)
        if request is None:
            raise analysis_run_error(
                f"Plot stage '{stage_kind}' resolved without a matching plot request."
            )
        command = [
            rscript,
            str(script),
            str(analysis_dir),
            plot_kind,
            ",".join(request.formats),
        ]
        if plot_kind == "run_duration":
            if plan.population_policy is None:
                raise analysis_run_error("Run-duration plot requires an experiment population policy.")
            command.extend([str(export_dir), plan.population_policy])
        elif plot_kind == "requirement_compliance":
            command.extend(
                [
                    request.estimand or "scenario",
                    "true" if request.include_intervals else "false",
                ]
            )
        elif plot_kind == "dimension_scores":
            command.extend(
                [
                    request.estimand or "opportunity",
                    request.aggregation_method or "arithmetic",
                    "true" if request.include_intervals else "false",
                ]
            )
        elif plot_kind == "dimension_profile":
            command.extend(
                [
                    request.estimand or "opportunity",
                    request.aggregation_method or "arithmetic",
                ]
            )
        elif plot_kind == "trajectory_run_metrics":
            command.append(request.metric or "tool_calls")
        elif plot_kind == "trajectory_tool_usage":
            command.append(str(request.top_n or 12))
        elif plot_kind == "trajectory_tool_composition":
            command.append(str(request.top_n or 12))
        elif plot_kind == "provider_pressure_feature_scores":
            command.append(str(request.top_n or 8))
        return tuple(command)

    raise analysis_run_error(f"Unsupported R analysis stage '{stage_kind}'.")


def _run_r_stage(
    command: Sequence[str],
    log_path: Path,
    progress: Callable[[str], None],
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("w", encoding="utf-8", newline="") as log_file:
            log_file.write(f"Command: {shlex.join(command)}\n\n")
            process = subprocess.Popen(
                list(command),
                cwd=_REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    log_file.write(line)
                    log_file.flush()
                    stripped = line.strip()
                    if stripped.startswith(_PROGRESS_PREFIX):
                        message = stripped[len(_PROGRESS_PREFIX):].strip()
                        if message:
                            progress(message)
            except KeyboardInterrupt:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
            return_code = process.wait()
    except OSError as exc:
        raise analysis_run_error(
            f"Could not execute analysis command '{command[0]}': {exc}"
        ) from exc

    if return_code != 0:
        raise analysis_run_error(
            f"R analysis stage failed with exit code {return_code}; see '{log_path}'.",
            exit_code=return_code if return_code > 0 else 1,
        )


def _bootstrap_for(plan: analysis_plan, kind: str) -> bootstrap_parameters:
    request = plan.analysis(kind)
    if request is not None and request.bootstrap is not None:
        return request.bootstrap
    return plan.bootstrap_defaults


def _scope_for_stage(kind: str) -> str:
    if kind in {"availability", "plot_availability"}:
        return AVAILABILITY_SCOPE
    if kind == "interaction":
        return INTERACTION_SCOPE
    if kind in {
        "trajectory",
        "plot_trajectory_run_metrics",
        "plot_trajectory_tool_usage",
        "plot_trajectory_error_rate",
        "plot_trajectory_tool_composition",
    }:
        return TRAJECTORY_SCOPE
    if kind in {
        "provider_pressure_summary",
        "plot_provider_pressure_pacing",
        "plot_provider_pressure_feature_scores",
        "plot_provider_pressure_admission_waits",
        "plot_provider_pressure_utilization_envelope",
        "plot_provider_pressure_utilization_by_subject",
        "plot_provider_pressure_wait_vs_actions",
    }:
        return PROVIDER_PRESSURE_SCOPE
    return COMPLIANCE_SCOPE


def _preflight_runtime(plan: analysis_plan) -> _runtime_context:
    rscript = shutil.which("Rscript")
    if rscript is None:
        raise analysis_run_error(
            "Required command 'Rscript' was not found on PATH.",
            exit_code=2,
        )

    for stage_kind in plan.resolved_stages:
        definition = _STAGE_DEFINITIONS[stage_kind]
        if definition.script_name is not None:
            script_path = _R_SCRIPT_ROOT / definition.script_name
            if not script_path.is_file():
                raise analysis_run_error(
                    f"Required R analysis script is missing: '{script_path}'.",
                    exit_code=2,
                )

    if any(stage.endswith("_workbook") for stage in plan.resolved_stages):
        try:
            xlsxwriter_version = version("XlsxWriter")
        except PackageNotFoundError as exc:
            raise analysis_run_error(
                "XlsxWriter is required by the requested workbook stage.",
                exit_code=2,
            ) from exc
    else:
        xlsxwriter_version = None

    try:
        completed = subprocess.run(
            [rscript, "--version"],
            cwd=_REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise analysis_run_error(
            f"Could not query Rscript version: {exc}",
            exit_code=2,
        ) from exc
    version_text = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0 or not version_text:
        raise analysis_run_error(
            "Could not determine the Rscript runtime version.",
            exit_code=2,
        )

    r_packages = _probe_r_packages(rscript)

    try:
        acmm_version = version("acmm")
    except PackageNotFoundError:
        acmm_version = "uninstalled-source-tree"

    return _runtime_context(
        rscript=rscript,
        rscript_version=version_text.splitlines()[0],
        python_version=platform.python_version(),
        acmm_version=acmm_version,
        xlsxwriter_version=xlsxwriter_version,
        r_packages=r_packages,
    )


def _probe_r_packages(rscript: str) -> dict[str, str]:
    packages = ("digest", "jsonlite", "yaml")
    expression = (
        "packages <- c("
        + ", ".join(f"'{package}'" for package in packages)
        + "); "
        "for (p in packages) { "
        "if (!requireNamespace(p, quietly=TRUE)) { "
        "cat(paste0('MISSING:', p, '\\n')); quit(status=3); }; "
        "cat(paste0(p, '=', as.character(packageVersion(p)), '\\n')); }"
    )
    try:
        completed = subprocess.run(
            [rscript, "-e", expression],
            cwd=_REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise analysis_run_error(
            f"Could not probe required R packages: {exc}",
            exit_code=2,
        ) from exc

    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        missing = [
            line.split(":", 1)[1]
            for line in output.splitlines()
            if line.startswith("MISSING:")
        ]
        if missing:
            raise analysis_run_error(
                "Required R package is not installed: " + ", ".join(missing) + ".",
                exit_code=2,
            )
        raise analysis_run_error(
            "Could not verify required R analysis packages.",
            exit_code=2,
        )

    versions: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        package, package_version = line.split("=", 1)
        if package in packages and package_version:
            versions[package] = package_version
    missing_versions = [package for package in packages if package not in versions]
    if missing_versions:
        raise analysis_run_error(
            "Could not determine R package version(s): "
            + ", ".join(missing_versions)
            + ".",
            exit_code=2,
        )
    return versions


def _validate_export_binding(manifest: Mapping[str, Any]) -> None:
    schema_version = str(manifest.get("schema_version", ""))
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise analysis_run_error(
            "Export manifest must define a source mapping.",
            exit_code=2,
        )
    source_kind = source.get("kind")
    if source_kind == "provider_pressure":
        if schema_version not in {"0.1", "0.2", "0.3", "0.4", "0.5", "0.6"}:
            raise analysis_run_error(
                "Provider Pressure analysis exports require schema_version '0.1', '0.2', '0.3', '0.4', '0.5', or '0.6'.",
                exit_code=2,
            )
        digest = source.get("source_digest_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise analysis_run_error(
                "Provider Pressure export manifest must define source.source_digest_sha256.",
                exit_code=2,
            )
        return

    if schema_version not in {"0.1", "0.2", "0.3", "0.4", "0.5"}:
        raise analysis_run_error(
            f"Unsupported analysis export schema_version '{schema_version}'.",
            exit_code=2,
        )
    experiment_id = source.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise analysis_run_error(
            "Export manifest must define a non-empty source.experiment_id.",
            exit_code=2,
        )


def _validate_plan_export_compatibility(
    plan: analysis_plan,
    manifest: Mapping[str, Any],
) -> None:
    source = manifest.get("source")
    source_kind = source.get("kind") if isinstance(source, dict) else None
    if plan.source_kind == "provider_pressure":
        if source_kind != "provider_pressure":
            raise analysis_run_error(
                "Provider Pressure analysis plans require a Provider Pressure export.",
                exit_code=2,
            )
        utilization_stages = {
            "plot_provider_pressure_utilization_envelope",
            "plot_provider_pressure_utilization_by_subject",
        }
        if utilization_stages.intersection(plan.resolved_stages):
            schema_version = str(manifest.get("schema_version", ""))
            if schema_version != "0.6":
                raise analysis_run_error(
                    "Provider Pressure utilization plots require export schema 0.6 "
                    "with utilization_summary.csv.",
                    exit_code=2,
                )
        return
    if source_kind == "provider_pressure":
        raise analysis_run_error(
            "Experiment analysis plans cannot be run against a Provider Pressure export.",
            exit_code=2,
        )
    if "trajectory" not in plan.resolved_stages:
        return
    schema_version = str(manifest.get("schema_version", ""))
    if schema_version != "0.3" or source_kind != "campaign":
        raise analysis_run_error(
            "Trajectory analysis requires Campaign analysis export schema 0.3 "
            "with normalized tool_calls.csv and tool_results.csv tables.",
            exit_code=2,
        )


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as exc:
        raise analysis_run_error(
            f"Could not read {label} '{path}': {exc}",
            exit_code=2,
        ) from exc
    if not isinstance(value, dict):
        raise analysis_run_error(
            f"{label.capitalize()} '{path}' must contain a YAML mapping.",
            exit_code=2,
        )
    return value


def _referenced_inputs(plan: analysis_plan) -> dict[str, dict[str, str]]:
    inputs: dict[str, dict[str, str]] = {}
    interaction = plan.analysis("interaction")
    if interaction is not None and interaction.design_path is not None:
        inputs["interaction_design"] = {
            "source_path": str(interaction.design_path),
            "sha256": _sha256_file(interaction.design_path),
        }
    return inputs


def _copy_referenced_inputs(plan: analysis_plan, staging_dir: Path) -> Path | None:
    interaction = plan.analysis("interaction")
    if interaction is None or interaction.design_path is None:
        return None
    inputs_dir = staging_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    destination = inputs_dir / "interaction_design.yaml"
    destination.write_bytes(interaction.design_path.read_bytes())
    return destination


def _implementation_files() -> dict[str, str]:
    candidates: list[Path] = []
    for directory in (
        _REPO_ROOT / "analysis" / "r" / "R",
        _REPO_ROOT / "analysis" / "r" / "scripts",
        _REPO_ROOT / "src" / "acmm" / "analysis" / "schemas",
    ):
        candidates.extend(path for path in directory.rglob("*") if path.is_file())
    for name in (
        "layout.py",
        "plan.py",
        "runner.py",
        "workbook.py",
        "availability_workbook.py",
        "interaction_workbook.py",
    ):
        candidates.append(_REPO_ROOT / "src" / "acmm" / "analysis" / name)

    result: dict[str, str] = {}
    for path in sorted(set(candidates)):
        relative = path.relative_to(_REPO_ROOT).as_posix()
        result[relative] = _sha256_file(path)
    return result


def _implementation_digest(files: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for path, sha256 in sorted(files.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _analysis_fingerprint(
    export_manifest_sha256: str,
    plan_sha256: str,
    implementation_digest: str,
    referenced_inputs: Mapping[str, Mapping[str, str]],
    runtime: _runtime_context,
) -> str:
    digest = hashlib.sha256()
    for label, value in (
        ("export_manifest", export_manifest_sha256),
        ("analysis_plan", plan_sha256),
        ("implementation", implementation_digest),
    ):
        digest.update(f"{label}:{value}\n".encode("utf-8"))
    for name, binding in sorted(referenced_inputs.items()):
        digest.update(
            f"input:{name}:{binding['sha256']}\n".encode("utf-8")
        )
    for label, value in (
        ("python", runtime.python_version),
        ("acmm", runtime.acmm_version),
        ("rscript", runtime.rscript_version),
        ("xlsxwriter", runtime.xlsxwriter_version or "not-required"),
    ):
        digest.update(f"runtime:{label}:{value}\n".encode("utf-8"))
    for package, package_version in sorted(runtime.r_packages.items()):
        digest.update(
            f"r-package:{package}:{package_version}\n".encode("utf-8")
        )
    return digest.hexdigest()


def _prepare_output_paths(
    target_dir: Path,
    staging_dir: Path,
    failed_dir: Path,
) -> None:
    for path, label in (
        (target_dir, "completed analysis"),
        (staging_dir, "staging analysis"),
        (failed_dir, "failed analysis"),
    ):
        if path.exists():
            raise analysis_run_error(
                f"Refusing to overwrite existing {label} directory '{path}'.",
                exit_code=2,
            )
    target_dir.parent.mkdir(parents=True, exist_ok=True)


def _publish_failed(staging_dir: Path, failed_dir: Path) -> None:
    if not staging_dir.exists():
        return
    try:
        staging_dir.replace(failed_dir)
    except OSError:
        pass


_PLOT_OUTPUT_STEM_BY_STAGE = {
    "plot_run_duration": "figures/operations/run_duration",
    "plot_requirement_compliance": "figures/compliance/requirement_compliance",
    "plot_dimension_scores": "figures/compliance/dimension_scores",
    "plot_dimension_profile": "figures/compliance/dimension_profile",
    "plot_availability": "figures/availability/availability",
    "plot_trajectory_run_metrics": "figures/trajectory/trajectory_run_metrics",
    "plot_trajectory_tool_usage": "figures/trajectory/trajectory_tool_usage",
    "plot_trajectory_error_rate": "figures/trajectory/trajectory_error_rate",
    "plot_trajectory_tool_composition": "figures/trajectory/trajectory_tool_composition",
    "plot_provider_pressure_pacing": "figures/provider_pressure/pacing_trajectory",
    "plot_provider_pressure_feature_scores": "figures/provider_pressure/feature_scores",
    "plot_provider_pressure_admission_waits": "figures/provider_pressure/admission_waits",
    "plot_provider_pressure_utilization_envelope": "figures/provider_pressure/utilization_envelope",
    "plot_provider_pressure_utilization_by_subject": "figures/provider_pressure/utilization_by_subject",
    "plot_provider_pressure_wait_vs_actions": "figures/provider_pressure/admission_wait_vs_actions",
}


def _expected_outputs_for_stage(
    stage_kind: str,
    plan: analysis_plan,
) -> tuple[str, ...]:
    stem = _PLOT_OUTPUT_STEM_BY_STAGE.get(stage_kind)
    if stem is None:
        return _STAGE_DEFINITIONS[stage_kind].expected_outputs

    plot_kind = {
        "plot_run_duration": "run_duration",
        "plot_requirement_compliance": "requirement_compliance",
        "plot_dimension_scores": "dimension_scores",
        "plot_dimension_profile": "dimension_profile",
        "plot_availability": "availability",
        "plot_trajectory_run_metrics": "trajectory_run_metrics",
        "plot_trajectory_tool_usage": "trajectory_tool_usage",
        "plot_trajectory_error_rate": "trajectory_error_rate",
        "plot_trajectory_tool_composition": "trajectory_tool_composition",
        "plot_provider_pressure_pacing": "provider_pressure_pacing",
        "plot_provider_pressure_feature_scores": "provider_pressure_feature_scores",
        "plot_provider_pressure_admission_waits": "provider_pressure_admission_waits",
        "plot_provider_pressure_utilization_envelope": "provider_pressure_utilization_envelope",
        "plot_provider_pressure_utilization_by_subject": "provider_pressure_utilization_by_subject",
        "plot_provider_pressure_wait_vs_actions": "provider_pressure_wait_vs_actions",
    }[stage_kind]
    request = plan.plot(plot_kind)
    if request is None:
        raise analysis_run_error(
            f"Plot stage '{stage_kind}' resolved without a matching plot request."
        )
    return tuple(f"{stem}.{format_name}" for format_name in request.formats)


def _validate_stage_outputs(
    analysis_dir: Path,
    definition: _stage_definition,
    expected_outputs: Sequence[str] | None = None,
) -> None:
    required = tuple(expected_outputs or definition.expected_outputs)
    missing = [
        relative
        for relative in required
        if not (analysis_dir / relative).is_file()
    ]
    if missing:
        raise analysis_run_error(
            f"Analysis stage '{definition.kind}' completed without required output(s): "
            + ", ".join(missing)
            + "."
        )


def _build_analysis_manifest(
    analysis_id: str,
    fingerprint: str,
    export_manifest: Mapping[str, Any],
    export_manifest_sha256: str,
    plan: analysis_plan,
    plan_sha256: str,
    implementation_files: Mapping[str, str],
    implementation_digest: str,
    referenced_inputs: Mapping[str, Mapping[str, str]],
    runtime: _runtime_context,
    completed_stages: list[dict[str, Any]],
    staging_dir: Path,
) -> dict[str, Any]:
    source = export_manifest["source"]
    source_binding: dict[str, Any] = {
        "export_schema_version": str(export_manifest["schema_version"]),
        "export_manifest_sha256": export_manifest_sha256,
        "kind": source.get("kind", "execution"),
    }
    for key in (
        "experiment_id",
        "execution_id",
        "campaign_id",
        "merge_id",
        "package_sha256",
        "contract_sha256",
        "design_digest",
        "source_digest_sha256",
        "lineage_count",
        "snapshot_count",
        "trajectory_schema_versions",
    ):
        if key in source:
            source_binding[key] = source[key]
    if "adjudication" in source:
        source_binding["adjudication"] = source["adjudication"]
    if "lineages" in source:
        source_binding["lineages"] = source["lineages"]

    artifacts = []
    for path in sorted(
        item
        for item in staging_dir.rglob("*")
        if item.is_file() and item.name != "analysis_manifest.yaml"
    ):
        artifacts.append(
            {
                "artifact": path.relative_to(staging_dir).as_posix(),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )

    inputs_manifest: dict[str, Any] = {}
    for name, binding in sorted(referenced_inputs.items()):
        inputs_manifest[name] = {
            "artifact": "inputs/interaction_design.yaml"
            if name == "interaction_design"
            else name,
            "sha256": binding["sha256"],
        }

    return {
        "schema_version": ANALYSIS_MANIFEST_SCHEMA_VERSION,
        "analysis": {
            "id": analysis_id,
            "fingerprint_sha256": fingerprint,
            "status": "complete",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        "source": source_binding,
        "plan": {
            "schema_version": str(plan.raw["schema_version"]),
            "artifact": "analysis_plan.yaml",
            "sha256": plan_sha256,
            "source_kind": plan.source_kind,
            **(
                {"population_policy": plan.population_policy}
                if plan.population_policy is not None
                else {}
            ),
            "requested_analyses": [request.kind for request in plan.analyses],
            "requested_reports": list(plan.reports),
            "requested_plots": [
                {
                    key: value
                    for key, value in {
                        "kind": request.kind,
                        "estimand": request.estimand,
                        "aggregation_method": request.aggregation_method,
                        "include_intervals": request.include_intervals,
                        "metric": request.metric,
                        "top_n": request.top_n,
                        "formats": list(request.formats),
                    }.items()
                    if value is not None
                }
                for request in plan.plots
            ],
            "resolved_stages": list(plan.resolved_stages),
            "referenced_inputs": inputs_manifest,
        },
        "implementation": {
            "sha256": implementation_digest,
            "files": [
                {"path": path, "sha256": sha256}
                for path, sha256 in sorted(implementation_files.items())
            ],
        },
        "runtime": {
            "python": runtime.python_version,
            "acmm": runtime.acmm_version,
            "rscript": runtime.rscript_version,
            "xlsxwriter": runtime.xlsxwriter_version,
            "r_packages": dict(sorted(runtime.r_packages.items())),
        },
        "stages": completed_stages,
        "artifacts": artifacts,
    }


def _write_yaml(path: Path, value: Mapping[str, Any]) -> None:
    text = yaml.safe_dump(dict(value), sort_keys=False, allow_unicode=False)
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
