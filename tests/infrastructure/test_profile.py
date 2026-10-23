from pathlib import Path

from acmm_infra.profile import load_agent_zero_profile, load_hermes_profile


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


def test_open_webui_profile_loads_repository_baseline() -> None:
    from acmm_infra.profile import load_open_webui_profile

    profile = load_open_webui_profile(ROOT)

    assert profile.expected_version == "0.11.4"
    assert profile.container_name == "acmm-open-webui"
    assert profile.image.endswith(
        "sha256:9591b13f13843c7721c2b8eaf7382846c81b3ffe126526d1888d1fed50c6a33f"
    )
    assert profile.ollama_base_url == "http://host.docker.internal:11434"
    assert profile.model_default == "nemotron-3-nano:30b-cloud"
    assert profile.webui_auth is False
    assert profile.secret_key_file == "/app/backend/data/.webui_secret_key"
    assert profile.ollama_api_enabled is True
    assert profile.openai_api_enabled is False
    assert profile.plugins_enabled is True
    assert profile.pip_install_frontmatter_requirements_enabled is False
    assert profile.telemetry_enabled is False
    assert profile.deployment_directory == Path("open-webui")
    assert profile.data_directory == Path("data")
    assert len(profile.tools) == 1
    assert profile.tools[0].id == "acmm_tools"
    assert profile.tools[0].name == "ACMM Tools"
    assert profile.tools[0].source == Path("tools/acmm_tools.py")



def test_agent_zero_profile_loads_repository_baseline() -> None:
    profile = load_agent_zero_profile(ROOT)

    assert profile.expected_version == "v2.14"
    assert profile.container_name == "acmm-agent-zero"
    assert profile.image.endswith(
        "sha256:b0c9763df7b8c4c5ddbdac9c101e8f84de8b7cdf9185203e446e2d7d8ebf2f1c"
    )
    assert profile.web_port == 50080
    assert profile.deployment_directory == Path("agent-zero")
    assert profile.data_directory == Path("usr")
    assert profile.agent_profile == "acmm"
    assert profile.model_preset == "Default"
    assert profile.model_provider == "ollama"
    assert profile.model_base_url == "http://host.docker.internal:11434"
    assert profile.model_default == "nemotron-3-nano:30b-cloud"
    assert profile.context_window == 64000
    assert profile.utility_model_provider == "ollama"
    assert profile.utility_model_default == "nemotron-3-nano:30b-cloud"
    assert profile.utility_context_window == 64000
    assert profile.embedding_model_provider == "huggingface"
    assert profile.embedding_model_default == "sentence-transformers/all-MiniLM-L6-v2"
    assert profile.experiment_isolation.tool_default == "block"
    assert profile.experiment_isolation.mcp_tool_default == "block"
    assert profile.experiment_isolation.automatic_chat_naming is False
    assert profile.plugins[0].name == "acmm_tools"
    assert profile.plugins[0].destination == Path("plugins/acmm_tools")
    assert profile.reset_scopes["configuration"] == (
        Path("settings.json"),
        Path("agents/acmm"),
        Path("plugins/_model_config/presets.yaml"),
    )
