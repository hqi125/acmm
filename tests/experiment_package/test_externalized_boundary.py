from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_core_has_no_built_in_research_experiment_ownership() -> None:
    assert not (ROOT / "experiments" / "examples").exists()
    assert not (ROOT / "src" / "acmm" / "pilots").exists()
    assert not (ROOT / "src" / "acmm" / "experiments").exists()

    for subject in ("hermes", "open-webui", "agent-zero"):
        assert not (ROOT / "infrastructure" / subject / "experiments").exists()
