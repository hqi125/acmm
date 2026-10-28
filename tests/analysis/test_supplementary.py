from acmm.analysis.supplementary import get_supplementary_analysis


def test_experiment_3_registers_separate_interaction_analysis() -> None:
    spec = get_supplementary_analysis(
        "controlled_recovery_boundary_interaction_v1"
    )

    assert spec is not None
    assert spec.script_name == "analyze_interactions.R"
    assert spec.workbook_name == "interaction_analysis.xlsx"


def test_other_experiments_have_no_supplementary_analysis_by_default() -> None:
    assert get_supplementary_analysis("example_experiment_v1") is None
