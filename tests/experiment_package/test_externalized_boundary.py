from __future__ import annotations

from pathlib import Path

from acmm.experiments.catalog import lookup_experiment_execution_binding


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "controlled_authoritative_state_interference_v1"


def test_externalized_authoritative_state_is_not_owned_by_main_repo() -> None:
    assert lookup_experiment_execution_binding(EXPERIMENT_ID) is None
    assert not (
        ROOT / "experiments" / "examples" / "controlled_authoritative_state_interference"
    ).exists()
    assert not (ROOT / "src" / "acmm" / "pilots" / "authoritative_state.py").exists()

    for subject in ("hermes", "open-webui", "agent-zero"):
        assert not (
            ROOT / "infrastructure" / subject / "experiments" / EXPERIMENT_ID
        ).exists()
