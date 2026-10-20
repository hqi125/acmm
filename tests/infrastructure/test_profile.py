from pathlib import Path

from acmm_infra.profile import load_hermes_profile


ROOT = Path(__file__).resolve().parents[2]


def test_hermes_profile_loads_repository_baseline() -> None:
    profile = load_hermes_profile(ROOT)

    assert profile.expected_version == "0.21.3"
    assert profile.container_name == "acmm-hermes"
    assert profile.model_provider == "custom"
    assert profile.model_base_url == "http://host.docker.internal:11434/v1"
    assert profile.model_api_mode == "chat_completions"
    assert profile.model_default == "nemotron-3-nano:30b-cloud"
    assert profile.context_window == 64000
    assert profile.reasoning_effort == "medium"
    assert profile.terminal_backend == "local"
    assert profile.blank_slate.enabled is True
    assert profile.blank_slate.minimal_toolsets == (
        "file",
        "skills",
        "terminal",
        "vision",
    )
    assert profile.blank_slate.max_turns == 90
    assert profile.blank_slate.compression_enabled is False
    assert profile.blank_slate.memory_enabled is False
    assert profile.blank_slate.user_profile_enabled is False
    assert profile.blank_slate.checkpoints_enabled is False
    assert profile.blank_slate.smart_model_routing_enabled is False
    assert profile.blank_slate.tool_progress == "all"
    assert profile.blank_slate.bundled_skills == "essential_only"
    assert profile.experiment_isolation.title_generation_enabled is False
    assert profile.experiment_isolation.background_review_enabled is False
    assert profile.experiment_isolation.tool_search_enabled == "off"
    assert profile.deployment_directory == Path("hermes")
    assert profile.data_directory == Path("data")
    assert profile.plugins[0].name == "acmm-tools"
    assert profile.plugins[0].enabled is True
    assert profile.plugins[0].allow_tool_override is False
    assert profile.plugins[0].toolsets == ("acmm",)
    assert "experiment" in profile.reset_scopes
    assert profile.reset_scopes["configuration"] == (Path("config.yaml"),)
