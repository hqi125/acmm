from pathlib import Path

from acmm_infra.profiles.open_webui import load_open_webui_profile


ROOT = Path(__file__).resolve().parents[2]


def test_open_webui_profile_loads_repository_baseline() -> None:
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
    assert profile.tools == ()
