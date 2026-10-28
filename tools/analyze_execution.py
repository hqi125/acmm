from __future__ import annotations

import argparse
import csv
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Sequence

import yaml

from acmm.analysis.availability_workbook import (
    AVAILABILITY_WORKBOOK_NAME,
    availability_workbook_error,
    build_availability_workbook,
)
from acmm.analysis.interaction_workbook import (
    INTERACTION_WORKBOOK_NAME,
    build_interaction_workbook,
    interaction_workbook_error,
)
from acmm.analysis.layout import (
    AVAILABILITY_SCOPE,
    COMPLIANCE_SCOPE,
    INTERACTION_SCOPE,
    analysis_data_dir,
    analysis_log_dir,
    analysis_output_dir,
)
from acmm.analysis.supplementary import get_supplementary_analysis
from acmm.analysis.workbook import (
    WORKBOOK_NAME,
    analysis_workbook_error,
    build_analysis_workbook,
)


BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 12345
CONFIDENCE_LEVEL = 0.95
MAIN_WORKBOOK_STAGE = 6
AVAILABILITY_ANALYSIS_STAGE = 7
AVAILABILITY_WORKBOOK_STAGE = 8
BASE_TOTAL_STAGES = AVAILABILITY_WORKBOOK_STAGE
STRICT_POLICY = "strict"
AVAILABLE_VALID_POLICY = "available-valid"

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXPORT_ROOT = _REPO_ROOT / "exports"
_R_SCRIPT_ROOT = _REPO_ROOT / "analysis" / "r" / "scripts"
_AVAILABILITY_SCRIPT_NAME = "analyze_availability.R"

_R_STAGES = (
    (
        "Requirement estimates",
        "analyze_export.R",
        "point_estimates.txt",
        "requirement_estimates.csv",
        (),
    ),
    (
        "Hierarchical uncertainty",
        "analyze_uncertainty.R",
        "hierarchical_intervals.txt",
        "requirement_intervals.csv",
        (
            str(BOOTSTRAP_REPLICATES),
            str(BOOTSTRAP_SEED),
            str(CONFIDENCE_LEVEL),
        ),
    ),
    (
        "Dimension scores",
        "analyze_dimensions.R",
        "dimension_scores.txt",
        "dimension_scores.csv",
        (),
    ),
    (
        "Dimension-score uncertainty",
        "analyze_dimension_uncertainty.R",
        "dimension_intervals.txt",
        "dimension_intervals.csv",
        (
            str(BOOTSTRAP_REPLICATES),
            str(BOOTSTRAP_SEED),
            str(CONFIDENCE_LEVEL),
        ),
    ),
)


class analysis_workflow_error(ValueError):
    """Raised when the repository analysis workflow cannot proceed safely."""


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as exc:
        raise analysis_workflow_error(
            f"Could not read {label} '{path}': {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise analysis_workflow_error(
            f"{label.capitalize()} '{path}' must contain a YAML mapping."
        )

    return value


def _load_execution_identity(execution_dir: Path) -> tuple[str, str]:
    manifest = _load_yaml(
        execution_dir / "experiment_execution.yaml",
        "execution manifest",
    )
    execution = manifest.get("execution")

    if not isinstance(execution, dict):
        raise analysis_workflow_error(
            "Execution manifest must define an 'execution' mapping."
        )

    execution_id = execution.get("id")
    experiment_id = execution.get("experiment_id")

    if not isinstance(execution_id, str) or not execution_id:
        raise analysis_workflow_error(
            "Execution manifest must define a non-empty execution ID."
        )

    if not isinstance(experiment_id, str) or not experiment_id:
        raise analysis_workflow_error(
            "Execution manifest must define a non-empty experiment ID."
        )

    expected_name = f"execution_{execution_id}"

    if execution_dir.name != expected_name:
        raise analysis_workflow_error(
            "Execution directory name does not match manifest execution ID: "
            f"expected '{expected_name}', found '{execution_dir.name}'."
        )

    return execution_id, experiment_id


def _resolve_program(name: str) -> str:
    path = shutil.which(name)

    if path is None:
        raise analysis_workflow_error(
            f"Required command '{name}' was not found on PATH."
        )

    return path


def _preflight(supplementary_script_name: str | None = None) -> tuple[str, str]:
    acmm_command = _resolve_program("acmm")
    rscript_command = _resolve_program("Rscript")

    if importlib.util.find_spec("xlsxwriter") is None:
        raise analysis_workflow_error(
            "Required Python package 'XlsxWriter' is not installed. "
            "Run 'python -m pip install -e \".[test]\"' from the repository root."
        )

    script_names = [stage[1] for stage in _R_STAGES]
    script_names.append(_AVAILABILITY_SCRIPT_NAME)
    if supplementary_script_name is not None:
        script_names.append(supplementary_script_name)

    for script_name in script_names:
        script_path = _R_SCRIPT_ROOT / script_name

        if not script_path.is_file():
            raise analysis_workflow_error(
                f"Required R analysis script is missing: '{script_path}'."
            )

    return acmm_command, rscript_command


def _validate_reusable_export(
    export_dir: Path,
    execution_id: str,
    experiment_id: str,
) -> None:
    for name in ("runs.csv", "judgments.csv", "export_manifest.yaml"):
        if not (export_dir / name).is_file():
            raise analysis_workflow_error(
                f"Existing export is incomplete; missing '{name}'."
            )

    manifest = _load_yaml(export_dir / "export_manifest.yaml", "export manifest")
    source = manifest.get("source")

    if not isinstance(source, dict):
        raise analysis_workflow_error(
            "Existing export manifest must define a 'source' mapping."
        )

    if (
        source.get("execution_id") != execution_id
        or source.get("experiment_id") != experiment_id
    ):
        raise analysis_workflow_error(
            "Existing export does not match the requested execution."
        )


def _resolve_population_policy(export_dir: Path) -> tuple[str, int, int]:
    runs_path = export_dir / "runs.csv"

    try:
        with runs_path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            required = {
                "execution_selected_run_count",
                "execution_resolved_run_count",
            }
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise analysis_workflow_error(
                    "runs.csv is missing execution population count columns required "
                    "for automatic strict-first population resolution."
                )

            selected_values: set[int] = set()
            resolved_values: set[int] = set()
            for row in reader:
                try:
                    selected_values.add(int(row["execution_selected_run_count"]))
                    resolved_values.add(int(row["execution_resolved_run_count"]))
                except (TypeError, ValueError) as exc:
                    raise analysis_workflow_error(
                        "runs.csv contains invalid execution population counts."
                    ) from exc
    except OSError as exc:
        raise analysis_workflow_error(
            f"Could not inspect analysis population in '{runs_path}': {exc}"
        ) from exc

    if len(selected_values) != 1 or len(resolved_values) != 1:
        raise analysis_workflow_error(
            "runs.csv must contain one consistent selected/resolved run-count pair."
        )

    selected_count = next(iter(selected_values))
    resolved_count = next(iter(resolved_values))
    if selected_count == resolved_count:
        return STRICT_POLICY, selected_count, resolved_count

    return AVAILABLE_VALID_POLICY, selected_count, resolved_count


def _clean_legacy_flat_analysis_outputs(output_dir: Path) -> None:
    if not output_dir.is_dir():
        return

    for path in output_dir.iterdir():
        if path.is_file() and path.suffix.lower() in {".csv", ".txt"}:
            try:
                path.unlink()
            except OSError as exc:
                raise analysis_workflow_error(
                    f"Could not remove legacy analysis artifact '{path}': {exc}"
                ) from exc


def _run_command(command: Sequence[str]) -> int:
    try:
        return subprocess.run(
            list(command),
            cwd=_REPO_ROOT,
            check=False,
        ).returncode
    except OSError as exc:
        raise analysis_workflow_error(
            f"Could not execute '{command[0]}': {exc}"
        ) from exc


def _failed_output_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.failed{output_path.suffix}")


def _run_and_save(command: Sequence[str], output_path: Path) -> int:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_name(f".{output_path.name}.tmp")
        failed_path = _failed_output_path(output_path)
        temp_path.unlink(missing_ok=True)
        failed_path.unlink(missing_ok=True)

        with temp_path.open("w", encoding="utf-8", newline="") as output_file:
            process = subprocess.Popen(
                list(command),
                cwd=_REPO_ROOT,
                stdout=output_file,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            try:
                _, stderr_output = process.communicate()
            except KeyboardInterrupt:
                process.terminate()
                try:
                    _, stderr_output = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    _, stderr_output = process.communicate()

                if stderr_output:
                    print(stderr_output, end="", file=sys.stderr, flush=True)
                temp_path.replace(failed_path)
                print("\nAnalysis command interrupted by user.", file=sys.stderr)
                return 130
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise analysis_workflow_error(
            f"Could not execute or save analysis output '{output_path}': {exc}"
        ) from exc

    if stderr_output:
        print(stderr_output, end="", file=sys.stderr, flush=True)

    return_code = process.returncode

    try:
        if return_code == 0:
            temp_path.replace(output_path)
        else:
            with temp_path.open("a", encoding="utf-8", newline="") as output_file:
                if stderr_output:
                    output_file.write("\n--- stderr ---\n")
                    output_file.write(stderr_output)
            temp_path.replace(failed_path)
    except OSError as exc:
        raise analysis_workflow_error(
            f"Could not publish analysis output '{output_path}': {exc}"
        ) from exc

    return return_code


def run_analysis_workflow(
    execution: str | Path,
) -> int:
    execution_dir = Path(execution).expanduser().resolve()

    if not execution_dir.is_dir():
        raise analysis_workflow_error(
            f"Execution directory does not exist: '{execution_dir}'."
        )

    execution_id, experiment_id = _load_execution_identity(execution_dir)
    supplementary = get_supplementary_analysis(experiment_id)
    supplementary_design_path = (
        _REPO_ROOT / supplementary.design_path
        if supplementary is not None
        else None
    )
    total_stages = BASE_TOTAL_STAGES + (2 if supplementary is not None else 0)
    if supplementary is None:
        acmm_command, rscript_command = _preflight()
    else:
        acmm_command, rscript_command = _preflight(supplementary.script_name)
        if supplementary_design_path is None or not supplementary_design_path.is_file():
            raise analysis_workflow_error(
                "Registered supplementary analysis design is missing: "
                f"'{supplementary_design_path}'."
            )
    export_dir = _EXPORT_ROOT / experiment_id / f"execution_{execution_id}"

    if export_dir.exists():
        print(f"[1/{total_stages}] Reusing existing analysis export...")
        _validate_reusable_export(export_dir, execution_id, experiment_id)
        print(f"      export: {export_dir}")
    else:
        print(f"[1/{total_stages}] Exporting execution...")
        return_code = _run_command(
            (
                acmm_command,
                "export",
                str(execution_dir),
                "--output-root",
                str(_EXPORT_ROOT),
            )
        )

        if return_code != 0:
            print(
                f"ERROR: acmm export failed with exit code {return_code}.",
                file=sys.stderr,
            )
            print("Analysis stopped. No R commands were executed.", file=sys.stderr)
            return return_code if return_code > 0 else 1

        _validate_reusable_export(export_dir, execution_id, experiment_id)

    output_dir = analysis_output_dir(export_dir)
    _clean_legacy_flat_analysis_outputs(output_dir)
    compliance_data_dir = analysis_data_dir(export_dir, COMPLIANCE_SCOPE)
    compliance_log_dir = analysis_log_dir(export_dir, COMPLIANCE_SCOPE)
    availability_log_dir = analysis_log_dir(export_dir, AVAILABILITY_SCOPE)
    interaction_log_dir = analysis_log_dir(export_dir, INTERACTION_SCOPE)

    population_policy, selected_count, resolved_count = _resolve_population_policy(
        export_dir
    )
    if population_policy == STRICT_POLICY:
        print(
            "      population: strict "
            f"({resolved_count}/{selected_count} selected planned runs resolved)"
        )
    else:
        print(
            "WARNING: strict population is unavailable: "
            f"{resolved_count}/{selected_count} selected planned runs resolved.",
            file=sys.stderr,
        )
        print(
            "WARNING: falling back automatically to available-valid compliance "
            "analysis; missing planned runs remain explicit and availability "
            "analysis will run separately.",
            file=sys.stderr,
        )

    for stage_index, (
        label,
        script_name,
        output_name,
        csv_output_name,
        arguments,
    ) in enumerate(
        _R_STAGES,
        start=2,
    ):
        output_path = compliance_log_dir / output_name
        csv_output_path = compliance_data_dir / csv_output_name
        command = (
            rscript_command,
            str(_R_SCRIPT_ROOT / script_name),
            str(export_dir),
            *arguments,
            str(csv_output_path),
            f"--population-policy={population_policy}",
        )

        print(f"[{stage_index}/{total_stages}] Running {label.lower()}...")
        return_code = _run_and_save(command, output_path)

        if return_code != 0:
            print(
                f"ERROR: {script_name} failed with exit code {return_code}.",
                file=sys.stderr,
            )
            print(
                f"Failed output: {_failed_output_path(output_path)}",
                file=sys.stderr,
            )
            print("Analysis stopped.", file=sys.stderr)
            return return_code if return_code > 0 else 1

        print(f"      output: {output_path}")
        print(f"      table:  {csv_output_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    workbook_path = output_dir / WORKBOOK_NAME
    workbook_failed_path = compliance_log_dir / "analysis_report.failed.txt"
    workbook_failed_path.unlink(missing_ok=True)
    print(f"[{MAIN_WORKBOOK_STAGE}/{total_stages}] Building Excel report...")

    try:
        build_analysis_workbook(export_dir, workbook_path)
    except analysis_workbook_error as exc:
        try:
            workbook_failed_path.parent.mkdir(parents=True, exist_ok=True)
            workbook_failed_path.write_text(
                f"{exc}\n",
                encoding="utf-8",
                newline="",
            )
        except OSError:
            pass

        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Failed output: {workbook_failed_path}", file=sys.stderr)
        print("Analysis stopped.", file=sys.stderr)
        return 1

    print(f"      output: {workbook_path}")

    availability_stage = AVAILABILITY_ANALYSIS_STAGE
    availability_output = availability_log_dir / "availability_analysis.txt"
    availability_command = (
        rscript_command,
        str(_R_SCRIPT_ROOT / _AVAILABILITY_SCRIPT_NAME),
        str(export_dir),
        str(BOOTSTRAP_REPLICATES),
        str(BOOTSTRAP_SEED),
        str(CONFIDENCE_LEVEL),
        str(output_dir),
        f"--population-policy={population_policy}",
    )
    print(f"[{availability_stage}/{total_stages}] Running availability analysis...")
    return_code = _run_and_save(availability_command, availability_output)
    if return_code != 0:
        print(
            f"ERROR: {_AVAILABILITY_SCRIPT_NAME} failed with exit code "
            f"{return_code}.",
            file=sys.stderr,
        )
        print(
            f"Failed output: {_failed_output_path(availability_output)}",
            file=sys.stderr,
        )
        print("Analysis stopped.", file=sys.stderr)
        return return_code if return_code > 0 else 1
    print(f"      output: {availability_output}")

    availability_workbook_path = output_dir / AVAILABILITY_WORKBOOK_NAME
    print(
        f"[{AVAILABILITY_WORKBOOK_STAGE}/{total_stages}] "
        "Building availability workbook..."
    )
    try:
        build_availability_workbook(export_dir, availability_workbook_path)
    except availability_workbook_error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Analysis stopped.", file=sys.stderr)
        return 1
    print(f"      output: {availability_workbook_path}")

    interaction_workbook_path = None
    if supplementary is not None:
        supplementary_stage = BASE_TOTAL_STAGES + 1
        supplementary_output = interaction_log_dir / supplementary.text_output_name
        supplementary_command = (
            rscript_command,
            str(_R_SCRIPT_ROOT / supplementary.script_name),
            str(export_dir),
            str(BOOTSTRAP_REPLICATES),
            str(BOOTSTRAP_SEED),
            str(CONFIDENCE_LEVEL),
            str(output_dir),
            str(supplementary_design_path),
            f"--population-policy={population_policy}",
        )
        print(
            f"[{supplementary_stage}/{total_stages}] "
            f"Running {supplementary.label}..."
        )
        return_code = _run_and_save(supplementary_command, supplementary_output)
        if return_code != 0:
            print(
                f"ERROR: {supplementary.script_name} failed with exit code "
                f"{return_code}.",
                file=sys.stderr,
            )
            print(
                f"Failed output: {_failed_output_path(supplementary_output)}",
                file=sys.stderr,
            )
            print("Analysis stopped.", file=sys.stderr)
            return return_code if return_code > 0 else 1
        print(f"      output: {supplementary_output}")

        interaction_workbook_path = output_dir / INTERACTION_WORKBOOK_NAME
        print(f"[{total_stages}/{total_stages}] Building interaction workbook...")
        try:
            build_interaction_workbook(
                export_dir,
                interaction_workbook_path,
                supplementary_design_path,
            )
        except interaction_workbook_error as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print("Analysis stopped.", file=sys.stderr)
            return 1
        print(f"      output: {interaction_workbook_path}")

    print("\nAnalysis workflow completed successfully.")
    print(f"  execution:       {execution_id}")
    print(f"  experiment:      {experiment_id}")
    population_label = population_policy
    if population_policy == AVAILABLE_VALID_POLICY:
        population_label += " (automatic fallback)"
    print(f"  population:      {population_label}")
    print(f"  export:          {export_dir}")
    print(f"  analysis output: {output_dir}")
    print(f"  Excel report:    {workbook_path}")
    print(f"  Availability:    {availability_workbook_path}")
    if interaction_workbook_path is not None:
        print(f"  Interaction:     {interaction_workbook_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export one completed ACMM execution and run the repository R analysis workflow."
        )
    )
    parser.add_argument("execution", help="Path to a completed execution_<id> directory.")
    args = parser.parse_args()

    try:
        return run_analysis_workflow(args.execution)
    except KeyboardInterrupt:
        print("\nAnalysis interrupted by user.", file=sys.stderr)
        return 130
    except analysis_workflow_error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
