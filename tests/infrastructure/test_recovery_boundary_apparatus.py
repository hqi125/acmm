from pathlib import Path

from acmm_infra.profiles.agent_zero import load_agent_zero_profile
from acmm_infra.profiles.experiments import (
    with_agent_zero_experiment,
    with_hermes_experiment,
    with_open_webui_experiment,
)
from acmm_infra.profiles.hermes import load_hermes_profile
from acmm_infra.profiles.open_webui import load_open_webui_profile


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "controlled_recovery_boundary_interaction_v1"


def test_recovery_boundary_apparatus_composes_for_all_subjects() -> None:
    hermes = with_hermes_experiment(
        ROOT,
        load_hermes_profile(ROOT),
        EXPERIMENT_ID,
    )
    open_webui = with_open_webui_experiment(
        ROOT,
        load_open_webui_profile(ROOT),
        EXPERIMENT_ID,
    )
    agent_zero = with_agent_zero_experiment(
        ROOT,
        load_agent_zero_profile(ROOT),
        EXPERIMENT_ID,
    )

    assert hermes.plugins[0].name == "acmm-tools"
    assert (hermes.plugins[0].source / "__init__.py").is_file()
    assert (hermes.plugins[0].source / "plugin.yaml").is_file()
    assert open_webui.tools[0].id == "acmm_tools"
    assert agent_zero.experiment_isolation.allowed_tools == (
        "plugin:acmm_tools:read_record",
        "plugin:acmm_tools:write_record",
        "plugin:acmm_tools:recover_write_record",
        "plugin:acmm_tools:force_write_record",
    )
