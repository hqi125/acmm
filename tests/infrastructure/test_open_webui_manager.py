import json
from dataclasses import replace
import shutil
from pathlib import Path

from acmm.experiment_package.provenance import (
    experiment_deployment_provenance,
    experiment_package_provenance,
)
from acmm_infra.open_webui import open_webui_manager
from acmm_infra.process import command_result
from acmm_infra.profiles.experiments import (
    apparatus_tree_sha256,
    with_open_webui_apparatus,
)
from acmm_infra.profiles.open_webui import load_open_webui_profile


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "manager_apparatus_fixture_v1"

EXTERNAL_PACKAGE_EXPERIMENT_ID = "external_package_doctor_v1"


def _apparatus_root(tmp_path: Path, experiment_id: str = EXPERIMENT_ID) -> Path:
    source = ROOT / "tools" / "fixtures" / "llm_behavioral_tool_smoke" / "open-webui"
    target = tmp_path / "apparatus-source" / "open-webui" / experiment_id
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    manifest = target / "apparatus.yaml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace("llm_behavioral_tool_smoke_v1", experiment_id)
    manifest.write_text(text, encoding="utf-8")
    return target


def _package_provenance(
    infrastructure_subject: str,
    apparatus_sha256: str,
) -> experiment_deployment_provenance:
    return experiment_deployment_provenance(
        package=experiment_package_provenance(
            experiment_id=EXTERNAL_PACKAGE_EXPERIMENT_ID,
            package_schema_version="0.1",
            package_sha256="1" * 64,
            contract_sha256="2" * 64,
            runtime_sha256="3" * 64,
        ),
        infrastructure_subject=infrastructure_subject,
        apparatus_sha256=apparatus_sha256,
    )


class fake_docker:
    def __init__(self) -> None:
        self.running = False
        self.image = load_open_webui_profile(ROOT).image
        self.version = "0.11.4"
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
        return [
            {
                "id": self.model,
                "info": {"context_length": 131072},
            }
        ]

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

    def delete_tool(self, token: str, tool_id: str):
        assert token == "test-token"
        return self.tools.pop(tool_id, None) is not None


def _manager(tmp_path: Path, *, experiment: bool = False):
    profile = load_open_webui_profile(ROOT)
    if experiment:
        profile = with_open_webui_apparatus(_apparatus_root(tmp_path), profile, EXPERIMENT_ID)
    docker = fake_docker()
    api = fake_open_webui_api(profile.model_default)
    return (
        open_webui_manager(
            ROOT,
            tmp_path,
            profile,
            docker,
            api,
            experiment_id=EXPERIMENT_ID if experiment else None,
            apparatus_source_sha256=(
                apparatus_tree_sha256(_apparatus_root(tmp_path)) if experiment else None
            ),
        ),
        docker,
        api,
    )


def _experiment_manager(
    tmp_path: Path,
    docker: fake_docker,
    api: fake_open_webui_api,
) -> open_webui_manager:
    profile = with_open_webui_apparatus(_apparatus_root(tmp_path), load_open_webui_profile(ROOT), EXPERIMENT_ID)
    return open_webui_manager(
        ROOT,
        tmp_path,
        profile,
        docker,
        api,
        experiment_id=EXPERIMENT_ID,
        apparatus_source_sha256=apparatus_tree_sha256(_apparatus_root(tmp_path)),
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
    assert result["tools"]["created"] == ()
    assert manager.docker.running is False


def test_apply_with_experiment_reconciles_one_composed_state(
    tmp_path: Path,
) -> None:
    manager, docker, api = _manager(tmp_path, experiment=True)

    result = manager.apply()

    assert result["experiment_id"] == EXPERIMENT_ID
    assert result["application_scope"] == "experiment"
    assert result["tools"]["created"] == ("acmm_tools",)
    assert docker.up_count == 1
    assert docker.down_count == 1
    assert "acmm_tools" in api.tools

    marker_path = manager.deployment_root / ".acmm-experiment-apparatus.json"
    state = json.loads(marker_path.read_text(encoding="utf-8"))
    assert state["experiment_id"] == EXPERIMENT_ID
    assert state["application_scope"] == "experiment"


def test_apply_reconciles_acmm_owned_compose_baseline(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    manager.initialize_deployment()
    compose = manager.deployment_root / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")

    manager.apply()

    expected = ROOT / "infrastructure" / "open-webui" / "defaults" / "compose.yaml"
    assert compose.read_bytes() == expected.read_bytes()


def test_apply_experiment_creates_then_preserves_managed_tool(tmp_path: Path) -> None:
    manager, docker, api = _manager(tmp_path, experiment=True)
    docker.running = True

    first = manager.apply_experiment(EXPERIMENT_ID)
    second = manager.apply_experiment(EXPERIMENT_ID)

    assert first["application_scope"] == "experiment"
    assert first["created"] == ("acmm_tools",)
    assert first["updated"] == ()
    assert second["created"] == ()
    assert second["unchanged"] == ("acmm_tools",)
    assert "class Tools:" in api.tools["acmm_tools"]["content"]


def test_apply_tools_records_tool_only_experiment_scope(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path, experiment=True)
    docker.running = True

    result = manager.apply_tools(EXPERIMENT_ID)

    assert result["application_scope"] == "tools"
    marker = json.loads(
        (manager.deployment_root / ".acmm-experiment-apparatus.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["application_scope"] == "tools"


def test_apply_tools_without_experiment_removes_experiment_tool(
    tmp_path: Path,
) -> None:
    baseline, docker, api = _manager(tmp_path)
    baseline.apply()
    docker.running = True
    experiment = _experiment_manager(tmp_path, docker, api)
    experiment.apply_tools(EXPERIMENT_ID)
    assert "acmm_tools" in api.tools

    baseline.apply_tools(None)

    assert "acmm_tools" not in api.tools
    assert not (
        baseline.deployment_root / ".acmm-experiment-apparatus.json"
    ).exists()


def test_apply_experiment_repairs_managed_tool_drift(tmp_path: Path) -> None:
    manager, docker, api = _manager(tmp_path, experiment=True)
    docker.running = True
    manager.apply_experiment(EXPERIMENT_ID)
    api.tools["acmm_tools"]["content"] = "class Tools:\n    pass\n"

    result = manager.apply_experiment(EXPERIMENT_ID)

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
    assert result["tools"]["created"] == ()
    assert docker.running is False


def test_status_exposes_model_context_capability_metadata(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)
    manager.apply()
    docker.running = True

    status = manager.status()
    backend = status["observed"]["backend"]

    assert backend["model_available"] is True
    assert backend["model_context_window"] == 131072
    assert backend["model_context_source"] == (
        "open_webui_model_catalog:info.context_length"
    )



def test_doctor_is_ready_for_matching_running_baseline(tmp_path: Path) -> None:
    manager, docker, _ = _manager(tmp_path)
    manager.apply()
    docker.running = True

    report = manager.doctor()

    assert report.is_ready


def test_doctor_detects_missing_managed_tool(tmp_path: Path) -> None:
    baseline, docker, api = _manager(tmp_path)
    baseline.apply()
    docker.running = True
    manager = _experiment_manager(tmp_path, docker, api)
    manager.apply_experiment(EXPERIMENT_ID)
    api.tools.clear()

    report = manager.doctor()

    assert not report.is_ready
    check = next(item for item in report.checks if item.name == "managed_tool:acmm_tools")
    assert not check.ok



def test_baseline_apply_removes_active_experiment_tool(tmp_path: Path) -> None:
    baseline, docker, api = _manager(tmp_path)
    baseline.apply()
    docker.running = True
    experiment = _experiment_manager(tmp_path, docker, api)
    experiment.apply_experiment(EXPERIMENT_ID)
    assert "acmm_tools" in api.tools

    docker.running = False
    baseline.apply()

    assert "acmm_tools" not in api.tools
    assert not (baseline.deployment_root / ".acmm-experiment-apparatus.json").exists()

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


def test_package_backed_doctor_uses_package_apparatus_identity(
    tmp_path: Path,
) -> None:
    profile = with_open_webui_apparatus(_apparatus_root(tmp_path), load_open_webui_profile(ROOT), EXPERIMENT_ID)
    apparatus_sha256 = apparatus_tree_sha256(_apparatus_root(tmp_path))
    docker = fake_docker()
    api = fake_open_webui_api(profile.model_default)
    manager = open_webui_manager(
        ROOT,
        tmp_path,
        profile,
        docker,
        api,
        experiment_id=EXTERNAL_PACKAGE_EXPERIMENT_ID,
        deployment_provenance=_package_provenance(
            "open-webui",
            apparatus_sha256,
        ),
    )
    manager.apply()
    docker.running = True

    report = manager.doctor()

    assert report.is_ready


def test_managed_environment_overrides_compose_after_baseline(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    manager.profile = replace(
        manager.profile,
        managed_environment_overrides=(
            ("OPEN_WEBUI_TOOL_SERVER_CONNECTIONS", '[{"url":"https://example.invalid"}]'),
            ("ACMM_CAPABILITY_MARKER", "enabled"),
        ),
    )

    managed = manager._managed_env()

    assert managed["OPEN_WEBUI_TOOL_SERVER_CONNECTIONS"] == (
        '[{"url":"https://example.invalid"}]'
    )
    assert managed["ACMM_CAPABILITY_MARKER"] == "enabled"


def test_managed_environment_overrides_reject_duplicate_keys(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    manager.profile = replace(
        manager.profile,
        managed_environment_overrides=(("DUP", "one"), ("DUP", "two")),
    )

    try:
        manager._managed_env()
    except Exception as exc:
        assert "unique keys" in str(exc)
    else:
        raise AssertionError("Expected duplicate managed environment keys to fail.")


def test_external_tool_server_enablement_releases_connection_ownership(
    tmp_path: Path,
) -> None:
    manager, _, _ = _manager(tmp_path)
    manager.profile = replace(
        manager.profile,
        experiment_isolation=replace(
            manager.profile.experiment_isolation,
            external_tool_servers_enabled=True,
        ),
    )

    desired = manager._desired_isolation_config()

    assert "tool_server.connections" not in desired
    assert desired["terminal_server.connections"] == []




def test_managed_persistent_config_overrides_fill_released_keys(
    tmp_path: Path,
) -> None:
    manager, _, _ = _manager(tmp_path)
    connection = [{"url": "https://example.invalid", "type": "mcp"}]
    manager.profile = replace(
        manager.profile,
        experiment_isolation=replace(
            manager.profile.experiment_isolation,
            external_tool_servers_enabled=True,
        ),
        managed_config_overrides=(("tool_server.connections", connection),),
    )

    desired = manager._desired_isolation_config()

    assert desired["tool_server.connections"] == connection


def test_managed_persistent_config_overrides_reject_baseline_key_collision(
    tmp_path: Path,
) -> None:
    manager, _, _ = _manager(tmp_path)
    manager.profile = replace(
        manager.profile,
        managed_config_overrides=(("web.search.enable", True),),
    )

    try:
        manager._desired_isolation_config()
    except Exception as exc:
        assert "must not override baseline-managed key" in str(exc)
    else:
        raise AssertionError("Expected persistent-config key collision to fail.")

def test_baseline_keeps_external_tool_server_connections_empty(
    tmp_path: Path,
) -> None:
    manager, _, _ = _manager(tmp_path)

    desired = manager._desired_isolation_config()

    assert desired["tool_server.connections"] == []
