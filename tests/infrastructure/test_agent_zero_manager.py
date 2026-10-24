import json
from pathlib import Path

from acmm_infra.agent_zero import agent_zero_manager
from acmm_infra.process import command_result
from acmm_infra.profiles.agent_zero import load_agent_zero_profile


ROOT = Path(__file__).resolve().parents[2]


class fake_docker:
    def __init__(self) -> None:
        self.running = False
        self.image = load_agent_zero_profile(ROOT).image
        self.version = "v2.14"
        self.up_count = 0
        self.down_count = 0

    def container_running(self) -> bool:
        return self.running

    def container_info(self):
        return self.running, self.image

    def compose_config(self):
        return command_result(("docker", "compose", "config"), 0, "", "")

    def up(self):
        self.running = True
        self.up_count += 1
        return command_result(("docker", "compose", "up"), 0, "", "")

    def down(self):
        self.running = False
        self.down_count += 1
        return command_result(("docker", "compose", "down"), 0, "", "")

    def docker_version(self):
        return command_result(("docker", "--version"), 0, "Docker version 29\n", "")

    def compose_version(self):
        return command_result(
            ("docker", "compose", "version"),
            0,
            "Docker Compose v5\n",
            "",
        )

    def exec_script(self, script: str):
        return command_result(("sh", "-lc", script), 0, self.version + "\n", "")

    def replace_container_tree(self, root: str, files):
        if not hasattr(self, "host_data_root"):
            raise AssertionError("fake Docker data root was not configured")
        prefix = "/a0/usr/"
        assert root.startswith(prefix)
        target = self.host_data_root / root[len(prefix):]
        if target.exists():
            import shutil
            shutil.rmtree(target)
        for relative, payload in files.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        return command_result(("docker", "exec"), 0, "", "")


class fake_api:
    def __init__(self, profile) -> None:
        self.profile = profile
        self.settings = {
            "agent_profile": "agent0",
            "mcp_servers": '{"mcpServers":{}}',
            "mcp_server_enabled": False,
            "a2a_server_enabled": False,
            "update_check_enabled": True,
            "chat_inherit_project": True,
            "rfc_auto_docker": True,
        }
        self.models = [profile.model_default]
        self.settings_updates = 0
        self.presets = []
        self.configured_preset = "Default"
        self.selected_preset = "Default"

    def get_settings(self):
        return {"settings": dict(self.settings)}

    def update_settings(self, settings):
        self.settings.update(settings)
        self.settings_updates += 1
        return {"settings": dict(self.settings)}

    def get_model_presets(self, *, agent_profile: str):
        assert agent_profile == self.profile.agent_profile
        return {
            "presets": list(self.presets),
            "configured_preset": self.configured_preset,
            "selected_preset": self.selected_preset,
        }

    def save_model_presets(self, presets):
        self.presets = list(presets)
        return {"ok": True, "presets": list(self.presets)}

    def select_model_preset(self, *, agent_profile: str, name: str):
        assert agent_profile == self.profile.agent_profile
        self.configured_preset = name
        self.selected_preset = name
        return {"ok": True, "selected_preset": name}

    def get_model_config(self, *, agent_profile: str):
        assert agent_profile == self.profile.agent_profile
        return {
            "configured_preset": self.profile.model_preset,
            "selected_preset": self.profile.model_preset,
            "model_configured_label": (
                f"{self.profile.model_provider} / {self.profile.model_default}"
            ),
            "config": {
                "chat_model": {
                    "provider": self.profile.model_provider,
                    "name": self.profile.model_default,
                    "api_base": self.profile.model_base_url,
                    "ctx_length": self.profile.context_window,
                },
                "utility_model": {
                    "provider": self.profile.utility_model_provider,
                    "name": self.profile.utility_model_default,
                    "api_base": self.profile.utility_model_base_url,
                    "ctx_length": self.profile.utility_context_window,
                },
                "embedding_model": {
                    "provider": self.profile.embedding_model_provider,
                    "name": self.profile.embedding_model_default,
                },
            },
        }

    def search_models(
        self,
        *,
        provider: str,
        model_type: str,
        api_base: str,
        query: str = "",
    ):
        assert provider == self.profile.model_provider
        assert model_type == "chat"
        assert api_base == self.profile.model_base_url
        return {
            "models": [model for model in self.models if query in model],
            "provider": provider,
            "source": "provider_endpoint",
            "error": "",
        }


def _manager(tmp_path: Path):
    profile = load_agent_zero_profile(ROOT)
    docker = fake_docker()
    api = fake_api(profile)
    manager = agent_zero_manager(ROOT, tmp_path, profile, docker, api=api)
    docker.host_data_root = manager.data_root
    manager._probe_ready = lambda: (True, "HTTP 200")
    return manager, docker, api


def test_initialize_deployment_installs_agent_zero_defaults(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    result = manager.initialize_deployment()

    assert result["compose"] == "created"
    assert result["env"] == "created"
    assert manager.data_root.is_dir()
    assert (manager.deployment_root / "compose.yaml").is_file()
    assert (manager.deployment_root / ".env").is_file()


def test_apply_reconciles_full_baseline_in_one_container_lifetime(tmp_path: Path) -> None:
    manager, docker, api = _manager(tmp_path)
    manager.initialize_deployment()
    env = manager.deployment_root / ".env"
    env.write_text(
        "LOCAL=value\nAGENT_ZERO_CHAT_MODEL_NAME=obsolete\n",
        encoding="utf-8",
    )

    result = manager.apply(timeout_seconds=1.0)
    text = env.read_text(encoding="utf-8")

    assert "LOCAL=value" in text
    assert "AGENT_ZERO_CHAT_MODEL_NAME" not in text
    assert "AGENT_ZERO_AGENT_PROFILE=acmm" in text
    assert "AGENT_ZERO_MCP_SERVER_ENABLED=false" in text
    assert result["plugins_synchronized"] == ("acmm_tools",)
    assert result["persistent_settings"] == "created"
    assert result["provisioning_service"] == "stopped after apply"
    assert docker.up_count == 1
    assert docker.down_count == 1
    assert docker.running is False
    assert api.settings_updates == 1

    destination = manager.data_root / "plugins" / "acmm_tools"
    assert (destination / "plugin.yaml").is_file()
    assert (destination / "tools" / "get_magic_number.py").is_file()
    assert (destination / "tools" / "add_numbers.py").is_file()

    assert (manager.data_root / "agents" / "acmm" / "agent.yaml").is_file()
    assert (
        manager.data_root
        / "agents"
        / "acmm"
        / "plugins"
        / "_tool_access"
        / "config.json"
    ).is_file()
    assert api.presets == manager._model_preset_payload()
    assert api.configured_preset == manager.profile.model_preset


def test_apply_tools_is_subset_and_repairs_plugin_drift(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    manager.apply(timeout_seconds=1.0)
    destination = manager.data_root / "plugins" / "acmm_tools" / "plugin.yaml"
    destination.write_text("name: drifted\n", encoding="utf-8")

    result = manager.apply_tools()

    assert result["plugins_synchronized"] == ("acmm_tools",)
    source = ROOT / "infrastructure" / "agent-zero" / "plugins" / "acmm_tools"
    assert destination.read_bytes() == (source / "plugin.yaml").read_bytes()


def test_apply_reconciles_acmm_owned_compose_baseline(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    manager.initialize_deployment()
    compose = manager.deployment_root / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")

    manager.apply(timeout_seconds=1.0)

    expected = ROOT / "infrastructure" / "agent-zero" / "defaults" / "compose.yaml"
    assert compose.read_bytes() == expected.read_bytes()


def test_apply_writes_restricted_acmm_profile_files(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)

    manager.apply(timeout_seconds=1.0)

    profile_root = manager.data_root / "agents" / "acmm" / "plugins"
    tool_policy = json.loads(
        (profile_root / "_tool_access" / "config.json").read_text(encoding="utf-8")
    )
    skills = json.loads(
        (profile_root / "_skills" / "config.json").read_text(encoding="utf-8")
    )
    memory = json.loads(
        (profile_root / "_memory" / "config.json").read_text(encoding="utf-8")
    )
    chat_naming = json.loads(
        (profile_root / "_chat_naming" / "config.json").read_text(encoding="utf-8")
    )

    assert tool_policy == {
        "mode": "custom",
        "default": "block",
        "mcp_default": "block",
        "allowed": [
            "plugin:acmm_tools:get_magic_number",
            "plugin:acmm_tools:add_numbers",
        ],
        "blocked": [],
    }
    assert skills["visibility_policy"]["default"] == "block"
    assert skills["visibility_policy"]["allowed"] == []
    assert memory["memory_recall_enabled"] is False
    assert memory["memory_memorize_enabled"] is False
    assert memory["memory_memorize_consolidation"] is False
    assert chat_naming == {
        "automatic_naming": False,
        "automatic_naming_mode": "once",
    }

    prompt_root = manager.data_root / "agents" / "acmm" / "prompts"
    source_root = ROOT / "infrastructure" / "agent-zero" / "profile-prompts"
    for name in (
        "agent.system.main.communication.md",
        "agent.system.main.solving.md",
        "fw.initial_user_message.md",
        "fw.initial_message.md",
    ):
        assert (prompt_root / name).read_bytes() == (source_root / name).read_bytes()


def test_up_waits_for_web_backend(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)

    manager.up(timeout_seconds=1.0)

    assert docker.running is True


def test_doctor_is_ready_for_matching_running_stage2_baseline(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)
    manager.apply(timeout_seconds=1.0)
    docker.running = True

    report = manager.doctor()

    assert report.is_ready
    names = {check.name for check in report.checks}
    assert "persistent_settings" in names
    assert "model_config" in names
    assert "experiment_model" in names


def test_doctor_detects_managed_plugin_drift(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)
    manager.apply(timeout_seconds=1.0)
    docker.running = True
    plugin = manager.data_root / "plugins" / "acmm_tools" / "plugin.yaml"
    plugin.write_text("name: drifted\n", encoding="utf-8")

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item for item in report.checks if item.name == "managed_plugin:acmm_tools"
    )
    assert not check.ok


def test_doctor_detects_application_file_drift(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)
    manager.apply(timeout_seconds=1.0)
    docker.running = True
    policy = (
        manager.data_root
        / "agents"
        / "acmm"
        / "plugins"
        / "_tool_access"
        / "config.json"
    )
    policy.write_text("{}\n", encoding="utf-8")

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item for item in report.checks if item.name == "application_baseline_files"
    )
    assert not check.ok


def test_doctor_detects_prompt_drift(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)
    manager.apply(timeout_seconds=1.0)
    docker.running = True
    prompt = (
        manager.data_root
        / "agents"
        / "acmm"
        / "prompts"
        / "agent.system.main.communication.md"
    )
    prompt.write_text("drifted\n", encoding="utf-8")

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item for item in report.checks if item.name == "application_baseline_files"
    )
    assert not check.ok


def test_doctor_detects_initial_user_message_prompt_drift(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)
    manager.apply(timeout_seconds=1.0)
    docker.running = True
    prompt = (
        manager.data_root
        / "agents"
        / "acmm"
        / "prompts"
        / "fw.initial_user_message.md"
    )
    prompt.write_text("drifted\n", encoding="utf-8")

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item for item in report.checks if item.name == "application_baseline_files"
    )
    assert not check.ok


def test_doctor_detects_initial_message_prompt_drift(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)
    manager.apply(timeout_seconds=1.0)
    docker.running = True
    prompt = (
        manager.data_root
        / "agents"
        / "acmm"
        / "prompts"
        / "fw.initial_message.md"
    )
    prompt.write_text("drifted\n", encoding="utf-8")

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item for item in report.checks if item.name == "application_baseline_files"
    )
    assert not check.ok


def test_doctor_detects_missing_experiment_model(tmp_path: Path) -> None:
    manager, docker, api = _manager(tmp_path)
    manager.apply(timeout_seconds=1.0)
    docker.running = True
    api.models = []

    report = manager.doctor()

    assert not report.is_ready
    check = next(item for item in report.checks if item.name == "experiment_model")
    assert not check.ok


def test_doctor_detects_agent_zero_image_drift(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)
    manager.apply(timeout_seconds=1.0)
    docker.running = True
    docker.image = "agent0ai/agent-zero:latest"

    report = manager.doctor()

    assert not report.is_ready
    check = next(item for item in report.checks if item.name == "container_image")
    assert not check.ok
