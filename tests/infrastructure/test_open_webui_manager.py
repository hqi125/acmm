from pathlib import Path

from acmm_infra.open_webui import open_webui_manager
from acmm_infra.process import command_result
from acmm_infra.profile import load_open_webui_profile


ROOT = Path(__file__).resolve().parents[2]


class fake_docker:
    def __init__(self) -> None:
        self.running = False
        self.image = load_open_webui_profile(ROOT).image
        self.version = "0.11.4"

    def container_running(self) -> bool:
        return self.running

    def container_info(self):
        return self.running, self.image

    def compose_config(self):
        return command_result(("docker", "compose", "config"), 0, "", "")

    def up(self):
        self.running = True
        return command_result(("docker", "compose", "up"), 0, "", "")

    def down(self):
        self.running = False
        return command_result(("docker", "compose", "down"), 0, "", "")

    def docker_version(self):
        return command_result(("docker", "--version"), 0, "Docker version 29\n", "")

    def compose_version(self):
        return command_result(("docker", "compose", "version"), 0, "Docker Compose v5\n", "")

    def exec(self, args):
        return command_result(tuple(args), 0, self.version + "\n", "")


class fake_open_webui_api:
    def __init__(self, model: str) -> None:
        self.model = model
        self.tools: dict[str, dict] = {}
        self.ollama_config = {
            "ENABLE_OLLAMA_API": False,
            "OLLAMA_BASE_URLS": ["http://old-ollama:11434"],
            "OLLAMA_API_CONFIGS": {},
        }
        self.openai_config = {
            "ENABLE_OPENAI_API": True,
            "OPENAI_API_BASE_URLS": ["https://api.openai.com/v1"],
            "OPENAI_API_KEYS": ["preserved-test-key"],
            "OPENAI_API_CONFIGS": {},
        }
        self.config = {
            "task.title.enable": True,
            "task.tags.enable": True,
            "memories.enable": True,
            "web.search.enable": True,
        }

    def get_readiness(self):
        return {"status": True}

    def authenticate_no_auth_admin(self):
        return {
            "token": "test-token",
            "email": "admin@localhost",
            "role": "admin",
        }

    def get_ollama_config(self, token: str):
        assert token == "test-token"
        return dict(self.ollama_config)

    def update_ollama_config(self, token: str, payload: dict):
        assert token == "test-token"
        self.ollama_config = dict(payload)
        return dict(self.ollama_config)

    def get_openai_config(self, token: str):
        assert token == "test-token"
        return dict(self.openai_config)

    def update_openai_config(self, token: str, payload: dict):
        assert token == "test-token"
        self.openai_config = dict(payload)
        return dict(self.openai_config)

    def get_config(self, token: str):
        assert token == "test-token"
        return dict(self.config)

    def import_config(self, token: str, config: dict):
        assert token == "test-token"
        self.config.update(config)
        return dict(self.config)

    def get_models(self, token: str):
        assert token == "test-token"
        return [{"id": self.model}]

    def get_tool(self, token: str, tool_id: str):
        assert token == "test-token"
        tool = self.tools.get(tool_id)
        return dict(tool) if tool is not None else None

    def create_tool(self, token: str, payload: dict):
        assert token == "test-token"
        stored = dict(payload)
        stored["access_grants"] = list(payload.get("access_grants", []))
        self.tools[payload["id"]] = stored
        return dict(stored)

    def update_tool(self, token: str, tool_id: str, payload: dict):
        assert token == "test-token"
        stored = dict(payload)
        self.tools[tool_id] = stored
        return dict(stored)


def _manager(tmp_path: Path):
    profile = load_open_webui_profile(ROOT)
    docker = fake_docker()
    api = fake_open_webui_api(profile.model_default)
    return (
        open_webui_manager(ROOT, tmp_path, profile, docker, api),
        docker,
        api,
    )


def test_initialize_deployment_installs_open_webui_defaults(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    result = manager.initialize_deployment()

    assert result["compose"] == "created"
    assert result["env"] == "created"
    assert manager.data_root.is_dir()
    assert (manager.deployment_root / "compose.yaml").is_file()
    assert (manager.deployment_root / ".env").is_file()


def test_apply_reconciles_open_webui_managed_environment(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    manager.initialize_deployment()
    env = manager.deployment_root / ".env"
    env.write_text("LOCAL=value\nOPEN_WEBUI_AUTH=true\n", encoding="utf-8")

    result = manager.apply()
    text = env.read_text(encoding="utf-8")

    assert result["webui_auth"] is False
    assert "LOCAL=value" in text
    assert "OPEN_WEBUI_AUTH=false" in text
    assert (
        "OPEN_WEBUI_SECRET_KEY_FILE=/app/backend/data/.webui_secret_key"
        in text
    )
    assert "OPEN_WEBUI_ENABLE_OPENAI_API=false" in text
    assert "OPEN_WEBUI_ENABLE_PLUGINS=true" in text
    assert "OPEN_WEBUI_ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS=false" in text
    assert "OPEN_WEBUI_ENABLE_TITLE_GENERATION=false" in text
    assert "OPEN_WEBUI_ENABLE_MEMORIES=false" in text
    assert "OPEN_WEBUI_ENABLE_WEB_SEARCH=false" in text
    assert result["application"]["ollama_changed"] is True
    assert result["application"]["openai_changed"] is True
    assert result["application"]["isolation_changed"] is True
    assert result["tools"]["created"] == ("acmm_tools",)
    assert manager.docker.running is False


def test_apply_reconciles_acmm_owned_compose_baseline(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    manager.initialize_deployment()
    compose = manager.deployment_root / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")

    manager.apply()

    expected = ROOT / "infrastructure" / "open-webui" / "defaults" / "compose.yaml"
    assert compose.read_bytes() == expected.read_bytes()


def test_apply_tools_creates_then_preserves_managed_tool(tmp_path: Path) -> None:
    manager, docker, api = _manager(tmp_path)
    docker.running = True

    first = manager.apply_tools()
    second = manager.apply_tools()

    assert first["created"] == ("acmm_tools",)
    assert first["updated"] == ()
    assert second["created"] == ()
    assert second["unchanged"] == ("acmm_tools",)
    assert "class Tools:" in api.tools["acmm_tools"]["content"]


def test_apply_tools_repairs_managed_tool_drift(tmp_path: Path) -> None:
    manager, docker, api = _manager(tmp_path)
    docker.running = True
    manager.apply_tools()
    api.tools["acmm_tools"]["content"] = "class Tools:\n    pass\n"

    result = manager.apply_tools()

    assert result["updated"] == ("acmm_tools",)
    assert "get_magic_number" in api.tools["acmm_tools"]["content"]


def test_up_waits_for_backend_readiness(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)

    manager.up(timeout_seconds=1.0)

    assert docker.running is True


def test_apply_reconciles_persistent_provider_config(tmp_path: Path) -> None:
    manager, docker, api = _manager(tmp_path)

    result = manager.apply(timeout_seconds=1.0)

    assert api.ollama_config == {
        "ENABLE_OLLAMA_API": True,
        "OLLAMA_BASE_URLS": ["http://host.docker.internal:11434"],
        "OLLAMA_API_CONFIGS": {},
    }
    assert api.openai_config["ENABLE_OPENAI_API"] is False
    assert api.openai_config["OPENAI_API_KEYS"] == ["preserved-test-key"]
    assert api.config["task.title.enable"] is False
    assert api.config["task.tags.enable"] is False
    assert api.config["memories.enable"] is False
    assert api.config["web.search.enable"] is False
    assert api.config["tool_server.connections"] == []
    assert api.config["terminal_server.connections"] == []
    assert result["tools"]["created"] == ("acmm_tools",)
    assert docker.running is False


def test_doctor_is_ready_for_matching_running_baseline(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)
    manager.apply()
    docker.running = True

    report = manager.doctor()

    assert report.is_ready


def test_doctor_detects_missing_managed_tool(tmp_path: Path) -> None:
    manager, docker, api = _manager(tmp_path)
    manager.apply()
    api.tools.clear()
    docker.running = True

    report = manager.doctor()

    assert not report.is_ready
    check = next(item for item in report.checks if item.name == "managed_tool:acmm_tools")
    assert not check.ok


def test_doctor_detects_open_webui_image_drift(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)
    manager.apply()
    docker.running = True
    docker.image = "ghcr.io/open-webui/open-webui:main"

    report = manager.doctor()

    assert not report.is_ready
    check = next(item for item in report.checks if item.name == "container_image")
    assert not check.ok


def test_doctor_detects_experiment_isolation_drift(tmp_path: Path) -> None:
    manager, docker, api = _manager(tmp_path)
    manager.apply()
    api.config["task.title.enable"] = True
    docker.running = True

    report = manager.doctor()

    assert not report.is_ready
    check = next(item for item in report.checks if item.name == "experiment_isolation")
    assert not check.ok
