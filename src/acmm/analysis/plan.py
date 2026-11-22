from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

import jsonschema
import yaml


ANALYSIS_PLAN_SCHEMA_VERSION = "0.1"
DEFAULT_BOOTSTRAP_REPLICATES = 2000
DEFAULT_BOOTSTRAP_SEED = 12345
DEFAULT_CONFIDENCE_LEVEL = 0.95

ANALYSIS_KINDS = (
    "requirement_estimates",
    "requirement_uncertainty",
    "dimension_scores",
    "dimension_uncertainty",
    "availability",
    "interaction",
)
REPORT_KINDS = (
    "compliance_workbook",
    "availability_workbook",
    "interaction_workbook",
)

_STAGE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "requirement_estimates": (),
    "requirement_uncertainty": ("requirement_estimates",),
    "dimension_scores": ("requirement_estimates",),
    "dimension_uncertainty": ("dimension_scores",),
    "availability": (),
    "interaction": (),
    "compliance_workbook": (
        "requirement_uncertainty",
        "dimension_uncertainty",
    ),
    "availability_workbook": ("availability",),
    "interaction_workbook": ("interaction",),
}


class analysis_plan_error(ValueError):
    """Raised when an analysis plan is malformed or internally inconsistent."""


@dataclass(frozen=True)
class bootstrap_parameters:
    replicates: int
    seed: int
    confidence_level: float


@dataclass(frozen=True)
class analysis_request:
    kind: str
    bootstrap: bootstrap_parameters | None = None
    design_path: Path | None = None


@dataclass(frozen=True)
class analysis_plan:
    path: Path
    raw_bytes: bytes
    raw: Mapping[str, Any]
    population_policy: str
    bootstrap_defaults: bootstrap_parameters
    analyses: tuple[analysis_request, ...]
    reports: tuple[str, ...]
    resolved_stages: tuple[str, ...]

    def analysis(self, kind: str) -> analysis_request | None:
        for request in self.analyses:
            if request.kind == kind:
                return request
        return None


def default_analysis_plan_schema_path() -> Path:
    return Path(__file__).resolve().parent / "schemas" / "analysis_plan.schema.json"


def load_analysis_plan(
    path: str | Path,
    schema_path: str | Path | None = None,
) -> analysis_plan:
    plan_path = Path(path).expanduser().resolve()

    try:
        raw_bytes = plan_path.read_bytes()
    except OSError as exc:
        raise analysis_plan_error(
            f"Could not read analysis plan '{plan_path}': {exc}"
        ) from exc

    try:
        value = yaml.safe_load(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise analysis_plan_error(
            f"Analysis plan '{plan_path}' is not valid UTF-8 YAML: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise analysis_plan_error("Analysis plan must contain a YAML mapping.")

    selected_schema = (
        Path(schema_path).expanduser().resolve()
        if schema_path is not None
        else default_analysis_plan_schema_path()
    )
    schema = _load_json_schema(selected_schema)

    try:
        jsonschema.Draft202012Validator(schema).validate(value)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path)
        prefix = f" at '{location}'" if location else ""
        raise analysis_plan_error(
            f"Analysis plan failed schema validation{prefix}: {exc.message}"
        ) from exc

    analyses_value = value["analyses"]
    report_value = value.get("reports", [])
    analysis_kinds = [item["kind"] for item in analyses_value]
    report_kinds = [item["kind"] for item in report_value]
    _reject_duplicates(analysis_kinds, "analysis")
    _reject_duplicates(report_kinds, "report")

    if "interaction_workbook" in report_kinds and "interaction" not in analysis_kinds:
        # Dependency closure can insert computational prerequisites, but interaction
        # requires a design path and therefore cannot be synthesized from a report alone.
        raise analysis_plan_error(
            "interaction_workbook requires an explicit interaction analysis with a design path."
        )

    defaults = value.get("defaults", {})
    default_bootstrap = _bootstrap_parameters(defaults.get("bootstrap", {}), None)
    requests: list[analysis_request] = []

    for item in analyses_value:
        kind = item["kind"]
        parameters = None
        design_path = None
        if kind in {
            "requirement_uncertainty",
            "dimension_uncertainty",
            "availability",
            "interaction",
        }:
            parameters = _bootstrap_parameters(item.get("bootstrap", {}), default_bootstrap)
        if kind == "interaction":
            design_path = _resolve_plan_relative_path(plan_path, item["design"])
            if not design_path.is_file():
                raise analysis_plan_error(
                    f"Interaction design does not exist: '{design_path}'."
                )
        requests.append(
            analysis_request(
                kind=kind,
                bootstrap=parameters,
                design_path=design_path,
            )
        )

    requested_stages = [*analysis_kinds, *report_kinds]
    resolved_stages = _resolve_dependencies(requested_stages)

    return analysis_plan(
        path=plan_path,
        raw_bytes=raw_bytes,
        raw=value,
        population_policy=value["population"]["policy"],
        bootstrap_defaults=default_bootstrap,
        analyses=tuple(requests),
        reports=tuple(report_kinds),
        resolved_stages=resolved_stages,
    )


def _load_json_schema(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise analysis_plan_error(
            f"Could not load analysis-plan schema '{path}': {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise analysis_plan_error(
            f"Analysis-plan schema '{path}' must contain a JSON object."
        )
    return value


def _bootstrap_parameters(
    override: Mapping[str, Any],
    inherited: bootstrap_parameters | None,
) -> bootstrap_parameters:
    base = inherited or bootstrap_parameters(
        replicates=DEFAULT_BOOTSTRAP_REPLICATES,
        seed=DEFAULT_BOOTSTRAP_SEED,
        confidence_level=DEFAULT_CONFIDENCE_LEVEL,
    )
    return bootstrap_parameters(
        replicates=int(override.get("replicates", base.replicates)),
        seed=int(override.get("seed", base.seed)),
        confidence_level=float(
            override.get("confidence_level", base.confidence_level)
        ),
    )


def _resolve_plan_relative_path(plan_path: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = plan_path.parent / candidate
    return candidate.resolve()


def _reject_duplicates(values: list[str], label: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise analysis_plan_error(
            f"Analysis plan contains duplicate {label} kind(s): "
            + ", ".join(duplicates)
            + "."
        )


def _resolve_dependencies(requested: list[str]) -> tuple[str, ...]:
    resolved: list[str] = []
    visiting: set[str] = set()
    complete: set[str] = set()

    def visit(stage: str) -> None:
        if stage in complete:
            return
        if stage in visiting:
            raise analysis_plan_error(
                f"Analysis dependency cycle detected at '{stage}'."
            )
        if stage not in _STAGE_DEPENDENCIES:
            raise analysis_plan_error(f"Unknown analysis stage '{stage}'.")

        visiting.add(stage)
        for dependency in _STAGE_DEPENDENCIES[stage]:
            visit(dependency)
        visiting.remove(stage)
        complete.add(stage)
        resolved.append(stage)

    for stage in requested:
        visit(stage)

    return tuple(resolved)
