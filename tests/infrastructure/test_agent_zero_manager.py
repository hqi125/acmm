import json
from pathlib import Path

from acmm_infra.agent_zero import agent_zero_manager
from acmm_infra.process import command_result
from acmm_infra.profiles.agent_zero import load_agent_zero_profile
from acmm_infra.profiles.experiments import with_agent_zero_experiment


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "controlled_tool_use_multiscenario_v1"


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

    def write_container_files(self, root: str, files):
        if not hasattr(self, "host_data_root"):
            raise AssertionError("fake Docker data root was not configured")
        prefix = "/a0/usr/"
        assert root.startswith(prefix)
        target = self.host_data_root / root[len(prefix):]
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


def _manager(tmp_path: Path, *, experiment: bool = False):
    profile = load_agent_zero_profile(ROOT)
    if experiment:
        profile = with_agent_zero_experiment(ROOT, profile, EXPERIMENT_ID)
    docker = fake_docker()
    api = fake_api(profile)
    manager = agent_zero_manager(
        ROOT,
        tmp_path,
        profile,
        docker,
        api=api,
        experiment_id=EXPERIMENT_ID if experiment else None,
    )
    docker.host_data_root = manager.data_root
    manager._probe_ready = lambda: (True, "HTTP 200")
    return manager, docker, api


def _experiment_manager(
    tmp_path: Path,
    docker: fake_docker,
    api: fake_api,
) -> agent_zero_manager:
    profile = with_agent_zero_experiment(
        ROOT,
        load_agent_zero_profile(ROOT),
        EXPERIMENT_ID,
    )
    manager = agent_zero_manager(
        ROOT,
        tmp_path,
        profile,
        docker,
        api=api,
        experiment_id=EXPERIMENT_ID,
    )
    docker.host_data_root = manager.data_root
    manager._probe_ready = lambda: (True, "HTTP 200")
    return manager


def test_initialize_deployment_installs_agent_zero_defaults(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    result = manager.initialize_deployment()

    assert result["compose"] == "created"
    assert result["env"] == "created"
    assert manager.data_root.is_dir()
    assert (manager.deployment_root / "compose.yaml").is_file()
    assert (manager.deployment_root / ".env").is_file()


def test_apply_with_experiment_reconciles_one_composed_state(
    tmp_path: Path,
) -> None:
    manager, docker, _ = _manager(tmp_path, experiment=True)

    result = manager.apply(timeout_seconds=1.0)

    assert result["experiment_id"] == EXPERIMENT_ID
    assert result["application_scope"] == "experiment"
    assert result["plugins_synchronized"] == ("acmm_tools",)
    assert docker.up_count == 1
    assert docker.down_count == 1

    marker_path = manager.deployment_root / ".acmm-experiment-apparatus.json"
    state = json.loads(marker_path.read_text(encoding="utf-8"))
    assert state["experiment_id"] == EXPERIMENT_ID
    assert state["application_scope"] == "experiment"


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
    assert result["plugins_synchronized"] == ()
    assert result["persistent_settings"] == "created"
    assert result["provisioning_service"] == "stopped after apply"
    assert docker.up_count == 1
    assert docker.down_count == 1
    assert docker.running is False
    assert api.settings_updates == 1

    destination = manager.data_root / "plugins" / "acmm_tools"
    assert not destination.exists()

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


def test_apply_experiment_repairs_plugin_drift(tmp_path: Path) -> None:
    baseline, docker, api = _manager(tmp_path)
    baseline.apply(timeout_seconds=1.0)
    manager = _experiment_manager(tmp_path, docker, api)
    manager.apply_experiment(EXPERIMENT_ID)
    destination = manager.data_root / "plugins" / "acmm_tools" / "plugin.yaml"
    destination.write_text("name: drifted\n", encoding="utf-8")

    result = manager.apply_experiment(EXPERIMENT_ID)

    assert result["plugins_synchronized"] == ("acmm_tools",)
    source = (
        ROOT
        / "infrastructure"
        / "agent-zero"
        / "experiments"
        / EXPERIMENT_ID
        / "plugins"
        / "acmm_tools"
    )
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
        "allowed": [],
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



def test_apply_experiment_installs_tool_policy_and_plugin(tmp_path: Path) -> None:
    baseline, docker, api = _manager(tmp_path)
    baseline.apply(timeout_seconds=1.0)
    manager = _experiment_manager(tmp_path, docker, api)

    result = manager.apply_experiment(EXPERIMENT_ID)

    assert result["experiment_id"] == EXPERIMENT_ID
    assert result["application_scope"] == "experiment"
    assert result["plugins_synchronized"] == ("acmm_tools",)
    plugin = manager.data_root / "plugins" / "acmm_tools" / "plugin.yaml"
    assert plugin.is_file()
    policy = (
        manager.data_root
        / "agents"
        / "acmm"
        / "plugins"
        / "_tool_access"
        / "config.json"
    )
    tool_policy = json.loads(policy.read_text(encoding="utf-8"))
    assert tool_policy["allowed"] == [
        "plugin:acmm_tools:get_magic_number",
        "plugin:acmm_tools:add_numbers",
    ]


def test_apply_tools_records_tool_only_experiment_scope(tmp_path: Path) -> None:
    baseline, docker, api = _manager(tmp_path)
    baseline.apply(timeout_seconds=1.0)
    manager = _experiment_manager(tmp_path, docker, api)

    prompt = (
        manager.data_root
        / "agents"
        / "acmm"
        / "prompts"
        / "agent.system.main.communication.md"
    )
    prompt.write_text("operator-local drift\n", encoding="utf-8")

    result = manager.apply_tools(EXPERIMENT_ID, timeout_seconds=1.0)

    assert result["application_scope"] == "tools"
    assert prompt.read_text(encoding="utf-8") == "operator-local drift\n"
    marker = json.loads(
        (manager.deployment_root / ".acmm-experiment-apparatus.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["application_scope"] == "tools"


def test_apply_tools_without_experiment_restores_baseline_tool_policy(
    tmp_path: Path,
) -> None:
    baseline, docker, api = _manager(tmp_path)
    baseline.apply(timeout_seconds=1.0)
    experiment = _experiment_manager(tmp_path, docker, api)
    experiment.apply_tools(EXPERIMENT_ID, timeout_seconds=1.0)

    baseline.apply_tools(None, timeout_seconds=1.0)

    plugin_root = baseline.data_root / "plugins" / "acmm_tools"
    assert not plugin_root.exists()
    policy = (
        baseline.data_root
        / "agents"
        / "acmm"
        / "plugins"
        / "_tool_access"
        / "config.json"
    )
    assert json.loads(policy.read_text(encoding="utf-8"))["allowed"] == []
    assert not (
        baseline.deployment_root / ".acmm-experiment-apparatus.json"
    ).exists()


def test_baseline_apply_removes_active_experiment_apparatus(tmp_path: Path) -> None:
    baseline, docker, api = _manager(tmp_path)
    baseline.apply(timeout_seconds=1.0)
    experiment = _experiment_manager(tmp_path, docker, api)
    experiment.apply_experiment(EXPERIMENT_ID)
    plugin_root = experiment.data_root / "plugins" / "acmm_tools"
    assert plugin_root.is_dir()

    baseline.apply(timeout_seconds=1.0)

    assert not plugin_root.exists()
    policy = (
        baseline.data_root
        / "agents"
        / "acmm"
        / "plugins"
        / "_tool_access"
        / "config.json"
    )
    assert json.loads(policy.read_text(encoding="utf-8"))["allowed"] == []
    assert not (baseline.deployment_root / ".acmm-experiment-apparatus.json").exists()

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
    baseline, docker, api = _manager(tmp_path)
    baseline.apply(timeout_seconds=1.0)
    manager = _experiment_manager(tmp_path, docker, api)
    manager.apply_experiment(EXPERIMENT_ID)
    docker.running = True
    plugin = manager.data_root / "plugins" / "acmm_tools" / "plugin.yaml"
    plugin.write_text("name: drifted\n", encoding="utf-8")

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item for item in report.checks if item.name == "managed_plugin:acmm_tools"
    )
    assert not check.ok


def test_doctor_ignores_agent_zero_runtime_plugin_state(tmp_path: Path) -> None:
    baseline, docker, api = _manager(tmp_path)
    baseline.apply(timeout_seconds=1.0)
    manager = _experiment_manager(tmp_path, docker, api)
    manager.apply_experiment(EXPERIMENT_ID)
    docker.running = True

    plugin_root = manager.data_root / "plugins" / "acmm_tools"
    (plugin_root / "config.json").write_text(
        '{"runtime": "managed by Agent Zero"}\n',
        encoding="utf-8",
    )
    (plugin_root / ".toggle-1").write_text("", encoding="utf-8")
    runtime_data = plugin_root / "data"
    runtime_data.mkdir()
    (runtime_data / "state.json").write_text("{}\n", encoding="utf-8")

    report = manager.doctor()

    check = next(
        item for item in report.checks if item.name == "managed_plugin:acmm_tools"
    )
    assert check.ok
    assert report.is_ready


def test_doctor_detects_managed_plugin_default_config_drift(tmp_path: Path) -> None:
    baseline, docker, api = _manager(tmp_path)
    baseline.apply(timeout_seconds=1.0)
    manager = _experiment_manager(tmp_path, docker, api)
    manager.apply_experiment(EXPERIMENT_ID)
    docker.running = True
    config = manager.data_root / "plugins" / "acmm_tools" / "default_config.yaml"
    config.write_text("magic_number: 999\n", encoding="utf-8")

    report = manager.doctor()

    check = next(
        item for item in report.checks if item.name == "managed_plugin:acmm_tools"
    )
    assert not check.ok
    assert not report.is_ready


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
