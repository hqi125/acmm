from __future__ import annotations

from pathlib import Path

import pytest

from acmm.analysis.plan import analysis_plan_error, load_analysis_plan


ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="")
    return path


def test_standard_plan_resolves_expected_stage_order() -> None:
    plan = load_analysis_plan(ROOT / "analysis" / "plans" / "standard_v0_1.yaml")

    assert plan.population_policy == "strict"
    assert plan.bootstrap_defaults.replicates == 2000
    assert plan.bootstrap_defaults.seed == 12345
    assert plan.bootstrap_defaults.confidence_level == 0.95
    assert plan.resolved_stages == (
        "requirement_estimates",
        "requirement_uncertainty",
        "dimension_scores",
        "dimension_uncertainty",
        "availability",
        "compliance_workbook",
        "availability_workbook",
    )


def test_plan_dependency_closure_inserts_prerequisites_deterministically(
    tmp_path: Path,
) -> None:
    plan_path = _write(
        tmp_path / "plan.yaml",
        """schema_version: '0.1'\npopulation:\n  policy: strict\nanalyses:\n  - kind: dimension_uncertainty\n""",
    )

    plan = load_analysis_plan(plan_path)

    assert plan.resolved_stages == (
        "requirement_estimates",
        "dimension_scores",
        "dimension_uncertainty",
    )


def test_plan_rejects_duplicate_analysis_kind(tmp_path: Path) -> None:
    plan_path = _write(
        tmp_path / "plan.yaml",
        """schema_version: '0.1'\npopulation:\n  policy: strict\nanalyses:\n  - kind: requirement_estimates\n  - kind: requirement_estimates\n""",
    )

    with pytest.raises(analysis_plan_error, match="duplicate analysis"):
        load_analysis_plan(plan_path)


def test_interaction_design_is_resolved_relative_to_plan(tmp_path: Path) -> None:
    design = _write(
        tmp_path / "interaction.yaml",
        "schema_version: '0.1'\nanalysis_kind: interaction\n",
    )
    plan_path = _write(
        tmp_path / "plan.yaml",
        """schema_version: '0.1'\npopulation:\n  policy: available-valid\nanalyses:\n  - kind: interaction\n    design: interaction.yaml\nreports:\n  - kind: interaction_workbook\n""",
    )

    plan = load_analysis_plan(plan_path)

    interaction = plan.analysis("interaction")
    assert interaction is not None
    assert interaction.design_path == design.resolve()
    assert plan.resolved_stages == ("interaction", "interaction_workbook")


def test_interaction_workbook_requires_explicit_design_analysis(tmp_path: Path) -> None:
    plan_path = _write(
        tmp_path / "plan.yaml",
        """schema_version: '0.1'\npopulation:\n  policy: strict\nanalyses:\n  - kind: requirement_estimates\nreports:\n  - kind: interaction_workbook\n""",
    )

    with pytest.raises(analysis_plan_error, match="requires an explicit interaction"):
        load_analysis_plan(plan_path)


def test_standard_v02_resolves_trajectory_and_plot_stages() -> None:
    plan = load_analysis_plan(ROOT / "analysis" / "plans" / "standard_v0_2.yaml")

    assert plan.schema_version == "0.2"
    assert plan.analysis("trajectory") is not None
    requirement_plot = plan.plot("requirement_compliance")
    assert requirement_plot is not None
    assert requirement_plot.estimand == "scenario"
    assert requirement_plot.include_intervals is True
    trajectory_plot = plan.plot("trajectory_tool_usage")
    assert trajectory_plot is not None
    assert trajectory_plot.top_n == 12
    assert plan.resolved_stages == (
        "requirement_estimates",
        "requirement_uncertainty",
        "dimension_scores",
        "dimension_uncertainty",
        "availability",
        "trajectory",
        "compliance_workbook",
        "availability_workbook",
        "plot_run_duration",
        "plot_requirement_compliance",
        "plot_dimension_scores",
        "plot_availability",
        "plot_trajectory_run_metrics",
        "plot_trajectory_tool_usage",
    )


def test_plot_dependency_closure_inserts_analysis_prerequisites(tmp_path: Path) -> None:
    plan_path = _write(
        tmp_path / "plan.yaml",
        """schema_version: '0.2'\npopulation:\n  policy: strict\nanalyses:\n  - kind: trajectory\nplots:\n  - kind: dimension_scores\n    estimand: scenario\n    aggregation_method: minimum\n  - kind: trajectory_run_metrics\n    metric: error_rate\n""",
    )

    plan = load_analysis_plan(plan_path)

    assert plan.resolved_stages == (
        "trajectory",
        "requirement_estimates",
        "dimension_scores",
        "dimension_uncertainty",
        "plot_dimension_scores",
        "plot_trajectory_run_metrics",
    )


def test_point_only_plot_does_not_force_bootstrap_uncertainty(tmp_path: Path) -> None:
    plan_path = _write(
        tmp_path / "plan.yaml",
        """schema_version: '0.2'
population:
  policy: strict
analyses:
  - kind: requirement_estimates
plots:
  - kind: dimension_scores
    estimand: scenario
    aggregation_method: arithmetic
    include_intervals: false
""",
    )

    plan = load_analysis_plan(plan_path)

    assert plan.resolved_stages == (
        "requirement_estimates",
        "dimension_scores",
        "plot_dimension_scores",
    )
