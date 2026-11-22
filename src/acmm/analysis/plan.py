from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

import jsonschema
import yaml


ANALYSIS_PLAN_SCHEMA_VERSION = "0.6"
ANALYSIS_PLAN_SCHEMA_VERSIONS = ("0.1", "0.2", "0.3", "0.4", "0.5", "0.6")
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
    "trajectory",
    "provider_pressure_summary",
)
REPORT_KINDS = (
    "compliance_workbook",
    "availability_workbook",
    "interaction_workbook",
)
PLOT_KINDS = (
    "run_duration",
    "requirement_compliance",
    "dimension_scores",
    "dimension_profile",
    "availability",
    "trajectory_run_metrics",
    "trajectory_tool_usage",
    "trajectory_error_rate",
    "trajectory_tool_composition",
    "provider_pressure_pacing",
    "provider_pressure_feature_scores",
    "provider_pressure_admission_waits",
    "provider_pressure_utilization_envelope",
    "provider_pressure_utilization_by_subject",
    "provider_pressure_wait_vs_actions",
)

_PLOT_STAGE_BY_KIND = {
    "run_duration": "plot_run_duration",
    "requirement_compliance": "plot_requirement_compliance",
    "dimension_scores": "plot_dimension_scores",
    "dimension_profile": "plot_dimension_profile",
    "availability": "plot_availability",
    "trajectory_run_metrics": "plot_trajectory_run_metrics",
    "trajectory_tool_usage": "plot_trajectory_tool_usage",
    "trajectory_error_rate": "plot_trajectory_error_rate",
    "trajectory_tool_composition": "plot_trajectory_tool_composition",
    "provider_pressure_pacing": "plot_provider_pressure_pacing",
    "provider_pressure_feature_scores": "plot_provider_pressure_feature_scores",
    "provider_pressure_admission_waits": "plot_provider_pressure_admission_waits",
    "provider_pressure_utilization_envelope": "plot_provider_pressure_utilization_envelope",
    "provider_pressure_utilization_by_subject": "plot_provider_pressure_utilization_by_subject",
    "provider_pressure_wait_vs_actions": "plot_provider_pressure_wait_vs_actions",
}

_STAGE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "requirement_estimates": (),
    "requirement_uncertainty": ("requirement_estimates",),
    "dimension_scores": ("requirement_estimates",),
    "dimension_uncertainty": ("dimension_scores",),
    "availability": (),
    "interaction": (),
    "trajectory": (),
    "provider_pressure_summary": (),
    "compliance_workbook": (
        "requirement_uncertainty",
        "dimension_uncertainty",
    ),
    "availability_workbook": ("availability",),
    "interaction_workbook": ("interaction",),
    "plot_run_duration": (),
    "plot_requirement_compliance": ("requirement_estimates",),
    "plot_dimension_scores": ("dimension_scores",),
    "plot_dimension_profile": ("dimension_scores",),
    "plot_availability": ("availability",),
    "plot_trajectory_run_metrics": ("trajectory",),
    "plot_trajectory_tool_usage": ("trajectory",),
    "plot_trajectory_error_rate": ("trajectory",),
    "plot_trajectory_tool_composition": ("trajectory",),
    "plot_provider_pressure_pacing": ("provider_pressure_summary",),
    "plot_provider_pressure_feature_scores": ("provider_pressure_summary",),
    "plot_provider_pressure_admission_waits": ("provider_pressure_summary",),
    "plot_provider_pressure_utilization_envelope": ("provider_pressure_summary",),
    "plot_provider_pressure_utilization_by_subject": ("provider_pressure_summary",),
    "plot_provider_pressure_wait_vs_actions": ("provider_pressure_summary",),
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
class plot_request:
    kind: str
    estimand: str | None = None
    aggregation_method: str | None = None
    include_intervals: bool | None = None
    metric: str | None = None
    top_n: int | None = None
    formats: tuple[str, ...] = ()


@dataclass(frozen=True)
class analysis_plan:
    path: Path
    raw_bytes: bytes
    raw: Mapping[str, Any]
    schema_version: str
    source_kind: str
    population_policy: str | None
    bootstrap_defaults: bootstrap_parameters
    analyses: tuple[analysis_request, ...]
    reports: tuple[str, ...]
    plots: tuple[plot_request, ...]
    plot_formats: tuple[str, ...]
    resolved_stages: tuple[str, ...]

    def analysis(self, kind: str) -> analysis_request | None:
        for request in self.analyses:
            if request.kind == kind:
                return request
        return None

    def plot(self, kind: str) -> plot_request | None:
        for request in self.plots:
            if request.kind == kind:
                return request
        return None


def default_analysis_plan_schema_path(schema_version: str = ANALYSIS_PLAN_SCHEMA_VERSION) -> Path:
    root = Path(__file__).resolve().parent / "schemas"
    if schema_version == "0.1":
        return root / "analysis_plan.schema.json"
    if schema_version == "0.2":
        return root / "analysis_plan_v0_2.schema.json"
    if schema_version == "0.3":
        return root / "analysis_plan_v0_3.schema.json"
    if schema_version == "0.4":
        return root / "analysis_plan_v0_4.schema.json"
    if schema_version == "0.5":
        return root / "analysis_plan_v0_5.schema.json"
    if schema_version == "0.6":
        return root / "analysis_plan_v0_6.schema.json"
    raise analysis_plan_error(
        f"Unsupported analysis plan schema_version '{schema_version}'."
    )


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

    schema_version = str(value.get("schema_version", ""))
    if schema_version not in ANALYSIS_PLAN_SCHEMA_VERSIONS:
        raise analysis_plan_error(
            f"Unsupported analysis plan schema_version '{schema_version}'. "
            f"Supported versions: {', '.join(ANALYSIS_PLAN_SCHEMA_VERSIONS)}."
        )

    selected_schema = (
        Path(schema_path).expanduser().resolve()
        if schema_path is not None
        else default_analysis_plan_schema_path(schema_version)
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

    source_kind = str(value.get("source", "experiment"))
    population_value = value.get("population")
    if source_kind == "experiment":
        if not isinstance(population_value, dict) or "policy" not in population_value:
            raise analysis_plan_error(
                "Experiment analysis plans must define population.policy."
            )
        population_policy: str | None = str(population_value["policy"])
    elif source_kind == "provider_pressure":
        if population_value is not None:
            raise analysis_plan_error(
                "Provider Pressure analysis plans must not define a GOBPFR population policy."
            )
        population_policy = None
    else:
        raise analysis_plan_error(f"Unsupported analysis source '{source_kind}'.")

    analyses_value = value["analyses"]
    report_value = value.get("reports", [])
    plot_value = value.get("plots", [])
    analysis_kinds = [item["kind"] for item in analyses_value]
    report_kinds = [item["kind"] for item in report_value]
    plot_kinds = [item["kind"] for item in plot_value]
    _reject_duplicates(analysis_kinds, "analysis")
    _reject_duplicates(report_kinds, "report")
    _reject_duplicates(plot_kinds, "plot")

    pressure_analysis_kinds = {"provider_pressure_summary"}
    pressure_plot_kinds = {
        "provider_pressure_pacing",
        "provider_pressure_feature_scores",
        "provider_pressure_admission_waits",
        "provider_pressure_utilization_envelope",
        "provider_pressure_utilization_by_subject",
        "provider_pressure_wait_vs_actions",
    }
    if source_kind == "provider_pressure":
        unsupported_analyses = sorted(set(analysis_kinds) - pressure_analysis_kinds)
        unsupported_plots = sorted(set(plot_kinds) - pressure_plot_kinds)
        if unsupported_analyses or unsupported_plots or report_kinds:
            details = []
            if unsupported_analyses:
                details.append("analyses=" + ",".join(unsupported_analyses))
            if unsupported_plots:
                details.append("plots=" + ",".join(unsupported_plots))
            if report_kinds:
                details.append("reports=" + ",".join(report_kinds))
            raise analysis_plan_error(
                "Provider Pressure plans may request only provider-pressure summary/plots ("
                + "; ".join(details)
                + ")."
            )
    else:
        pressure_requests = sorted(
            (set(analysis_kinds) & pressure_analysis_kinds)
            | (set(plot_kinds) & pressure_plot_kinds)
        )
        if pressure_requests:
            raise analysis_plan_error(
                "Experiment analysis plans cannot request Provider Pressure stages: "
                + ", ".join(pressure_requests)
                + "."
            )

    if "interaction_workbook" in report_kinds and "interaction" not in analysis_kinds:
        raise analysis_plan_error(
            "interaction_workbook requires an explicit interaction analysis with a design path."
        )

    defaults = value.get("defaults", {})
    default_bootstrap = _bootstrap_parameters(defaults.get("bootstrap", {}), None)
    default_plot_formats = _plot_formats(
        defaults.get("plot_formats"),
        ("png",) if schema_version in {"0.3", "0.4", "0.5", "0.6"} else ("png", "pdf"),
    )
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

    plots: list[plot_request] = []
    for item in plot_value:
        kind = item["kind"]
        plots.append(
            plot_request(
                kind=kind,
                estimand=item.get("estimand"),
                aggregation_method=item.get("aggregation_method"),
                include_intervals=(
                    bool(item.get("include_intervals", True))
                    if kind in {"requirement_compliance", "dimension_scores"}
                    else None
                ),
                metric=item.get("metric"),
                top_n=int(item["top_n"]) if "top_n" in item else None,
                formats=_plot_formats(item.get("formats"), default_plot_formats),
            )
        )

    requested_stages = [*analysis_kinds, *report_kinds]
    for request in plots:
        if request.kind == "requirement_compliance" and request.include_intervals:
            requested_stages.append("requirement_uncertainty")
        elif request.kind == "dimension_scores" and request.include_intervals:
            requested_stages.append("dimension_uncertainty")
        requested_stages.append(_PLOT_STAGE_BY_KIND[request.kind])
    resolved_stages = _resolve_dependencies(requested_stages)

    return analysis_plan(
        path=plan_path,
        raw_bytes=raw_bytes,
        raw=value,
        schema_version=schema_version,
        source_kind=source_kind,
        population_policy=population_policy,
        bootstrap_defaults=default_bootstrap,
        analyses=tuple(requests),
        reports=tuple(report_kinds),
        plots=tuple(plots),
        plot_formats=default_plot_formats,
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


def _plot_formats(value: Any, inherited: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return inherited
    formats = tuple(dict.fromkeys(str(item).lower() for item in value))
    if not formats:
        raise analysis_plan_error("At least one plot format is required.")
    unsupported = [item for item in formats if item not in {"png", "pdf"}]
    if unsupported:
        raise analysis_plan_error(
            "Unsupported plot format(s): " + ", ".join(unsupported) + "."
        )
    return formats


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
