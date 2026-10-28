from acmm.analysis.supplementary import get_supplementary_analysis


def test_experiment_3_registers_separate_interaction_analysis() -> None:
    spec = get_supplementary_analysis(
        "controlled_recovery_boundary_interaction_v1"
    )

    assert spec is not None
    assert spec.analysis_kind == "interaction"
    assert spec.script_name == "analyze_interactions.R"
    assert spec.workbook_name == "interaction_analysis.xlsx"
    assert spec.design_path.endswith("interaction_analysis.yaml")


def test_other_experiments_have_no_supplementary_analysis_by_default() -> None:
    assert get_supplementary_analysis("example_experiment_v1") is None
    assert get_supplementary_analysis(
        "controlled_authoritative_state_interference_v1"
    ) is None
