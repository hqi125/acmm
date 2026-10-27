from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Sequence

import yaml

from acmm.analysis.workbook import (
    WORKBOOK_NAME,
    analysis_workbook_error,
    build_analysis_workbook,
)


BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 12345
CONFIDENCE_LEVEL = 0.95
TOTAL_STAGES = 6
POPULATION_POLICIES = ("strict", "available-valid")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXPORT_ROOT = _REPO_ROOT / "exports"
_R_SCRIPT_ROOT = _REPO_ROOT / "analysis" / "r" / "scripts"
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


def _preflight() -> tuple[str, str]:
    acmm_command = _resolve_program("acmm")
    rscript_command = _resolve_program("Rscript")

    if importlib.util.find_spec("xlsxwriter") is None:
        raise analysis_workflow_error(
            "Required Python package 'XlsxWriter' is not installed. "
            "Run 'python -m pip install -e \".[test]\"' from the repository root."
        )

    for _, script_name, _, _, _ in _R_STAGES:
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
    *,
    population_policy: str = "strict",
) -> int:
    if population_policy not in POPULATION_POLICIES:
        raise analysis_workflow_error(
            "population_policy must be one of: "
            + ", ".join(POPULATION_POLICIES)
            + "."
        )

    execution_dir = Path(execution).expanduser().resolve()

    if not execution_dir.is_dir():
        raise analysis_workflow_error(
            f"Execution directory does not exist: '{execution_dir}'."
        )

    execution_id, experiment_id = _load_execution_identity(execution_dir)
    acmm_command, rscript_command = _preflight()
    export_dir = _EXPORT_ROOT / experiment_id / f"execution_{execution_id}"

    if export_dir.exists():
        print(f"[1/{TOTAL_STAGES}] Reusing existing analysis export...")
        _validate_reusable_export(export_dir, execution_id, experiment_id)
        print(f"      export: {export_dir}")
    else:
        print(f"[1/{TOTAL_STAGES}] Exporting execution...")
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

    analysis_output_dir = export_dir / "analysis_output"

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
        output_path = analysis_output_dir / output_name
        csv_output_path = analysis_output_dir / csv_output_name
        command = (
            rscript_command,
            str(_R_SCRIPT_ROOT / script_name),
            str(export_dir),
            *arguments,
            str(csv_output_path),
            f"--population-policy={population_policy}",
        )

        print(f"[{stage_index}/{TOTAL_STAGES}] Running {label.lower()}...")
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

    analysis_output_dir.mkdir(parents=True, exist_ok=True)
    workbook_path = analysis_output_dir / WORKBOOK_NAME
    workbook_failed_path = analysis_output_dir / "analysis_report.failed.txt"
    workbook_failed_path.unlink(missing_ok=True)
    print(f"[{TOTAL_STAGES}/{TOTAL_STAGES}] Building Excel report...")

    try:
        build_analysis_workbook(export_dir, workbook_path)
    except analysis_workbook_error as exc:
        try:
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

    print("\nAnalysis workflow completed successfully.")
    print(f"  execution:       {execution_id}")
    print(f"  experiment:      {experiment_id}")
    print(f"  population:      {population_policy}")
    print(f"  export:          {export_dir}")
    print(f"  analysis output: {analysis_output_dir}")
    print(f"  Excel report:    {workbook_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export one completed ACMM execution and run the repository R analysis workflow."
        )
    )
    parser.add_argument("execution", help="Path to a completed execution_<id> directory.")
    parser.add_argument(
        "--population-policy",
        choices=POPULATION_POLICIES,
        default="strict",
        help=(
            "Analysis population policy. 'strict' requires every selected planned "
            "run to resolve; 'available-valid' analyzes selected scientifically valid "
            "runs and reports missing planned slots explicitly."
        ),
    )
    args = parser.parse_args()

    try:
        return run_analysis_workflow(
            args.execution,
            population_policy=args.population_policy,
        )
    except KeyboardInterrupt:
        print("\nAnalysis interrupted by user.", file=sys.stderr)
        return 130
    except analysis_workflow_error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
