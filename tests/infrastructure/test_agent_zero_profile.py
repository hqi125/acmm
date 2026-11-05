from pathlib import Path

from acmm_infra.subjects.agent_zero.profile import load_agent_zero_profile


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
    assert profile.model_default == "gpt-oss:120b-cloud"
    assert profile.context_window == 64000
    assert profile.utility_model_provider == "ollama"
    assert profile.utility_model_default == "gpt-oss:120b-cloud"
    assert profile.utility_context_window == 64000
    assert profile.embedding_model_provider == "huggingface"
    assert profile.embedding_model_default == "sentence-transformers/all-MiniLM-L6-v2"
    assert profile.experiment_isolation.tool_default == "block"
    assert profile.experiment_isolation.mcp_tool_default == "block"
    assert profile.experiment_isolation.automatic_chat_naming is False
    assert profile.experiment_isolation.allowed_tools == ()
    assert profile.reset_scopes["configuration"] == (
        Path("settings.json"),
        Path("agents/acmm"),
        Path("plugins/_model_config/presets.yaml"),
    )

def test_agent_zero_profile_prompts_form_one_coherent_two_field_contract() -> None:
    prompt_root = ROOT / "infrastructure" / "agent-zero" / "profile-prompts"

    initial_user = (prompt_root / "fw.initial_user_message.md").read_text(encoding="utf-8")
    solving = (prompt_root / "agent.system.main.solving.md").read_text(encoding="utf-8")
    communication = (
        prompt_root / "agent.system.main.communication.md"
    ).read_text(encoding="utf-8")
    tools = (prompt_root / "agent.system.tools.md").read_text(encoding="utf-8")
    response = (prompt_root / "agent.system.tool.response.md").read_text(encoding="utf-8")
    repeat = (prompt_root / "fw.msg_repeat.md").read_text(encoding="utf-8")

    assert initial_user.strip() == "Hello!"
    assert "framework bootstrap context only" in solving
    assert "tool_name` and `tool_result" in solving
    assert "[EXTRAS]" in solving
    assert "most recent later real user request" in solving
    assert "exactly these top-level" in communication
    assert "ignore" in communication.lower()
    assert "thoughts" in communication
    assert "{{tools}}" in tools
    assert "Some inherited tool examples" in tools
    assert "thoughts" not in response.lower()
    assert '"tool_name":"response"' in response
    assert "do not send the same json" in repeat.lower()
