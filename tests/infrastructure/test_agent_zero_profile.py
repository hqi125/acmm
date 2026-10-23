from pathlib import Path

from acmm_infra.profiles.agent_zero import load_agent_zero_profile


ROOT = Path(__file__).resolve().parents[2]


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
