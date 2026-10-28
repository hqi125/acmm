"""Registry for experiment-registered supplementary ACMM analyses."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class supplementary_analysis_spec:
    experiment_id: str
    analysis_kind: str
    label: str
    script_name: str
    text_output_name: str
    workbook_name: str
    design_path: str


_SPECS = {
    "controlled_recovery_boundary_interaction_v1": supplementary_analysis_spec(
        experiment_id="controlled_recovery_boundary_interaction_v1",
        analysis_kind="interaction",
        label="interaction analysis",
        script_name="analyze_interactions.R",
        text_output_name="interaction_analysis.txt",
        workbook_name="interaction_analysis.xlsx",
        design_path=(
            "experiments/examples/controlled_recovery_boundary_interaction/"
            "interaction_analysis.yaml"
        ),
    ),
}


def get_supplementary_analysis(
    experiment_id: str,
) -> supplementary_analysis_spec | None:
    """Return the supplementary analysis registered for an experiment, if any."""

    return _SPECS.get(experiment_id)
