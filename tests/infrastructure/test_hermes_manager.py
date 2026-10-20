from __future__ import annotations

import json
from pathlib import Path

import pytest

from acmm_infra.docker import docker_error
from acmm_infra.hermes import hermes_manager, infrastructure_error
from acmm_infra.process import command_result
from acmm_infra.profile import load_hermes_profile


ROOT = Path(__file__).resolve().parents[2]


class fake_docker:
    def __init__(self) -> None:
        self.running = False
        self.version = "hermes-agent 0.21.3"
        self.model_provider = "custom"
        self.model_base_url = "http://host.docker.internal:11434/v1"
        self.model_api_mode = "chat_completions"
        self.context_window = 64000
        self.model_default = "nemotron-3-nano:30b-cloud"
        self.reasoning_effort = "medium"
        self.terminal_backend = "local"
        self.platform_toolsets_cli = [
            "file",
            "skills",
            "terminal",
            "vision",
        ]
        self.disabled_toolsets = [
            "browser",
            "code_execution",
            "cron",
            "delegation",
            "kanban",
            "web",
        ]
        self.expected_disabled_toolsets = list(
            self.disabled_toolsets
        )
        self.max_turns = 90
        self.compression_enabled = False
        self.memory_enabled = False
        self.user_profile_enabled = False
        self.checkpoints_enabled = False
        self.smart_model_routing_enabled = False
        self.tool_progress = "all"
        self.title_generation_enabled = False
        self.background_review_enabled = False
        self.tool_search_enabled = "off"
        self.bundled_skills_opt_out = True
        self.bundled_skill_manifest = ["hermes-agent"]
        self.essential_skills = ["hermes-agent"]
        self.plugins_enabled = ["acmm-tools"]
        self.image = "nousresearch/hermes-agent:v2026.9.14"
        self.exec_calls: list[tuple[str, ...]] = []
        self.exec_script_calls: list[tuple[str, float]] = []
        self.provision_calls: list[tuple[str, float]] = []
        self.provision_output_modes: list[str] = []
        self.provision_result: command_result | None = None
        self.exec_script_result: command_result | None = None

    def docker_version(self) -> command_result:
        return _ok(("docker", "--version"), "Docker version 27")

    def compose_version(self) -> command_result:
        return _ok(("docker", "compose", "version"), "Docker Compose version v2")

    def compose_config(self) -> command_result:
        return _ok(("docker", "compose", "config"), "")

    def container_info(self) -> tuple[bool, str | None]:
        return self.running, self.image

    def container_running(self) -> bool:
        return self.running

    def container_image(self) -> str | None:
        return self.image

    def exec(self, args) -> command_result:
        call = tuple(args)
        self.exec_calls.append(call)

        if call == ("hermes", "--version"):
            return _ok(call, self.version)

        if call == (
            "hermes",
            "config",
            "get",
            "model.provider",
            "--json",
        ):
            return _ok(call, json.dumps(self.model_provider))

        if call == (
            "hermes",
            "config",
            "get",
            "model.base_url",
            "--json",
        ):
            return _ok(call, json.dumps(self.model_base_url))

        if call == (
            "hermes",
            "config",
            "get",
            "model.api_mode",
            "--json",
        ):
            return _ok(call, json.dumps(self.model_api_mode))

        if call == (
            "hermes",
            "config",
            "get",
            "model.context_length",
            "--json",
        ):
            return _ok(call, json.dumps(self.context_window))

        if call == (
            "hermes",
            "config",
            "get",
            "agent.reasoning_effort",
            "--json",
        ):
            return _ok(call, json.dumps(self.reasoning_effort))

        if call == (
            "hermes",
            "config",
            "get",
            "terminal.backend",
            "--json",
        ):
            return _ok(call, json.dumps(self.terminal_backend))

        if call == (
            "hermes",
            "config",
            "get",
            "model.default",
            "--json",
        ):
            return _ok(call, json.dumps(self.model_default))

        if call == (
            "hermes",
            "config",
            "get",
            "plugins.enabled",
            "--json",
        ):
            return _ok(call, json.dumps(self.plugins_enabled))

        if call[:4] == (
            "hermes",
            "config",
            "set",
            "model.provider",
        ):
            self.model_provider = call[4]
            return _ok(call, "updated")

        if call[:4] == (
            "hermes",
            "config",
            "set",
            "model.base_url",
        ):
            self.model_base_url = call[4]
            return _ok(call, "updated")

        if call[:4] == (
            "hermes",
            "config",
            "set",
            "model.api_mode",
        ):
            self.model_api_mode = call[4]
            return _ok(call, "updated")

        if call[:4] == (
            "hermes",
            "config",
            "set",
            "model.context_length",
        ):
            self.context_window = int(call[4])
            return _ok(call, "updated")

        if call[:4] == (
            "hermes",
            "config",
            "set",
            "agent.reasoning_effort",
        ):
            self.reasoning_effort = call[4]
            return _ok(call, "updated")

        if call[:4] == (
            "hermes",
            "config",
            "set",
            "terminal.backend",
        ):
            self.terminal_backend = call[4]
            return _ok(call, "updated")

        if call[:4] == (
            "hermes",
            "config",
            "set",
            "model.default",
        ):
            self.model_default = call[4]
            return _ok(call, "updated")

        if call[:3] == ("hermes", "plugins", "enable"):
            if call[3] not in self.plugins_enabled:
                self.plugins_enabled.append(call[3])
            return _ok(call, "enabled")

        if call[:3] == ("hermes", "plugins", "disable"):
            self.plugins_enabled = [
                item for item in self.plugins_enabled if item != call[3]
            ]
            return _ok(call, "disabled")

        return _ok(call, "")

    def exec_script(
        self,
        script: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> command_result:
        self.exec_script_calls.append((script, timeout_seconds))

        if self.exec_script_result is not None:
            return self.exec_script_result

        output = "\n".join(
            [
                f"__ACMM__hermes_version={self.version}",
                f"__ACMM__model_provider={json.dumps(self.model_provider)}",
                f"__ACMM__model_base_url={json.dumps(self.model_base_url)}",
                f"__ACMM__model_api_mode={json.dumps(self.model_api_mode)}",
                f"__ACMM__context_window={json.dumps(self.context_window)}",
                f"__ACMM__model_default={json.dumps(self.model_default)}",
                f"__ACMM__reasoning_effort={json.dumps(self.reasoning_effort)}",
                f"__ACMM__terminal_backend={json.dumps(self.terminal_backend)}",
                (
                    "__ACMM__platform_toolsets_cli="
                    + json.dumps(self.platform_toolsets_cli)
                ),
                (
                    "__ACMM__disabled_toolsets="
                    + json.dumps(self.disabled_toolsets)
                ),
                (
                    "__ACMM__expected_disabled_toolsets="
                    + json.dumps(self.expected_disabled_toolsets)
                ),
                f"__ACMM__max_turns={json.dumps(self.max_turns)}",
                (
                    "__ACMM__compression_enabled="
                    + json.dumps(self.compression_enabled)
                ),
                (
                    "__ACMM__memory_enabled="
                    + json.dumps(self.memory_enabled)
                ),
                (
                    "__ACMM__user_profile_enabled="
                    + json.dumps(self.user_profile_enabled)
                ),
                (
                    "__ACMM__checkpoints_enabled="
                    + json.dumps(self.checkpoints_enabled)
                ),
                (
                    "__ACMM__smart_model_routing_enabled="
                    + json.dumps(self.smart_model_routing_enabled)
                ),
                f"__ACMM__tool_progress={json.dumps(self.tool_progress)}",
                (
                    "__ACMM__title_generation_enabled="
                    + json.dumps(self.title_generation_enabled)
                ),
                (
                    "__ACMM__background_review_enabled="
                    + json.dumps(self.background_review_enabled)
                ),
                (
                    "__ACMM__tool_search_enabled="
                    + json.dumps(self.tool_search_enabled)
                ),
                (
                    "__ACMM__bundled_skills_opt_out="
                    + json.dumps(self.bundled_skills_opt_out)
                ),
                (
                    "__ACMM__bundled_skill_manifest="
                    + json.dumps(self.bundled_skill_manifest)
                ),
                (
                    "__ACMM__essential_skills="
                    + json.dumps(self.essential_skills)
                ),
                "__ACMM__config_path=/opt/data/config.yaml",
                "__ACMM__env_path=/opt/data/.env",
                f"__ACMM__plugins_enabled={json.dumps(self.plugins_enabled)}",
                "__ACMM__plugin_override:acmm-tools=false",
            ]
        )
        return _ok(("exec-script",), output)

    def provision(
        self,
        script: str,
        timeout_seconds: float,
        *,
        output_mode: str = "normal",
        output=None,
    ) -> command_result:
        self.provision_calls.append((script, timeout_seconds))
        self.provision_output_modes.append(output_mode)

        full_apply = "model.provider" in script

        if output_mode == "normal" and output is not None:
            if full_apply:
                output("[Hermes 1/14] set model.provider")
                output("✓ Set model.provider = custom")
            else:
                output(
                    "[Hermes 1/2] set tools.tool_search.enabled"
                )
                output("✓ Set tools.tool_search.enabled = off")
            output("[Hermes] Provisioning batch complete.")

        if self.provision_result is not None:
            return self.provision_result

        if full_apply:
            self.model_provider = "custom"
            self.model_base_url = "http://host.docker.internal:11434/v1"
            self.model_api_mode = "chat_completions"
            self.context_window = 64000
            self.model_default = "nemotron-3-nano:30b-cloud"
            self.reasoning_effort = "medium"
            self.terminal_backend = "local"
            self.platform_toolsets_cli = [
                "file",
                "skills",
                "terminal",
                "vision",
            ]
            self.disabled_toolsets = list(
                self.expected_disabled_toolsets
            )
            self.max_turns = 90
            self.compression_enabled = False
            self.memory_enabled = False
            self.user_profile_enabled = False
            self.checkpoints_enabled = False
            self.smart_model_routing_enabled = False
            self.tool_progress = "all"
            self.title_generation_enabled = False
            self.background_review_enabled = False
            self.bundled_skills_opt_out = True
            self.bundled_skill_manifest = ["hermes-agent"]
            self.essential_skills = ["hermes-agent"]

        self.tool_search_enabled = "off"
        self.plugins_enabled = ["acmm-tools"]
        return _ok(("provision",), "")


def _ok(args: tuple[str, ...], stdout: str) -> command_result:
    return command_result(
        args=args,
        returncode=0,
        stdout=stdout,
        stderr="",
    )


def _manager(tmp_path: Path, docker: fake_docker | None = None):
    profile = load_hermes_profile(ROOT)
    docker_instance = docker or fake_docker()
    manager = hermes_manager(
        repo_root=ROOT,
        runtime_root=tmp_path,
        profile=profile,
        docker=docker_instance,
    )
    return manager, docker_instance


def test_apply_synchronizes_plugin_and_configures_hermes(tmp_path: Path) -> None:
    manager, docker = _manager(tmp_path)
    docker.model_provider = "auto"
    docker.model_default = "old-model"
    docker.plugins_enabled = []

    result = manager.apply()

    deployed = manager.data_root / "plugins" / "acmm-tools"
    source = ROOT / "infrastructure" / "hermes" / "plugins" / "acmm-tools"

    assert deployed.is_dir()
    assert (deployed / "plugin.yaml").read_bytes() == (source / "plugin.yaml").read_bytes()
    assert result["model_provider"] == "custom"
    assert result["model_base_url"] == "http://host.docker.internal:11434/v1"
    assert result["model_api_mode"] == "chat_completions"
    assert result["context_window"] == 64000
    assert result["reasoning_effort"] == "medium"
    assert result["terminal_backend"] == "local"
    assert result["model_default"] == "nemotron-3-nano:30b-cloud"
    assert result["blank_slate"] is True
    assert result["blank_slate_toolsets"] == (
        "file",
        "skills",
        "terminal",
        "vision",
    )
    assert result["experiment_isolation"] == {
        "title_generation_enabled": False,
        "background_review_enabled": False,
        "tool_search_enabled": "off",
    }
    assert docker.model_provider == "custom"
    assert docker.model_base_url == "http://host.docker.internal:11434/v1"
    assert docker.model_api_mode == "chat_completions"
    assert docker.context_window == 64000
    assert docker.reasoning_effort == "medium"
    assert docker.terminal_backend == "local"
    assert docker.model_default == "nemotron-3-nano:30b-cloud"
    assert "acmm-tools" in docker.plugins_enabled
    assert len(docker.provision_calls) == 1
    script, timeout_seconds = docker.provision_calls[0]
    assert timeout_seconds == 120.0
    assert "model.provider" in script
    assert "model.base_url" in script
    assert "model.api_mode" in script
    assert "model.context_length" in script
    assert "model.default" in script
    assert "agent.reasoning_effort" in script
    assert "terminal.backend" in script
    assert "auxiliary.title_generation.enabled" in script
    assert "auxiliary.background_review.enabled" in script
    assert "tools.tool_search.enabled" in script
    assert "/opt/hermes/bin/hermes" in script
    assert "plugins enable acmm-tools --no-allow-tool-override" in script
    assert "apply Blank Slate policy" in script
    assert "hermes_cli.setup" in script
    assert "_blank_slate_minimal_toolsets" in script
    assert "_blank_slate_minimize_config" in script
    assert "hermes_cli.config import load_config, save_config" not in script
    assert "skills opt-out --remove --yes" in script
    assert "sync_skills" in script
    assert "/command/s6-setuidgid hermes" not in script
    assert "/opt/hermes/.venv/bin/python" in script
    assert "/opt/hermes/.venv/bin/python" in script
    assert "'acmm'" in script
    assert result["provisioning_operations"] == 14
    assert docker.provision_output_modes == ["normal"]


def test_status_reports_desired_and_observed_state(tmp_path: Path) -> None:
    manager, docker = _manager(tmp_path)
    manager.apply()
    docker.running = True

    status = manager.status()

    assert status["desired"]["hermes_version"] == "0.21.3"
    assert status["observed"]["container_running"] is True
    assert status["observed"]["model_default"] == "nemotron-3-nano:30b-cloud"
    assert status["observed"]["terminal_backend"] == "local"
    assert status["observed"]["platform_toolsets_cli"] == [
        "file",
        "skills",
        "terminal",
        "vision",
    ]
    assert "acmm" not in status["observed"]["disabled_toolsets"]
    assert status["observed"]["max_turns"] == 90
    assert status["observed"]["memory_enabled"] is False
    assert status["observed"]["bundled_skills_opt_out"] is True
    assert status["observed"]["bundled_skill_manifest"] == [
        "hermes-agent"
    ]
    assert status["plugins"][0]["synchronized"] is True
    assert status["plugins"][0]["enabled_observed"] is True
    assert status["plugins"][0]["allow_tool_override_desired"] is False
    assert status["plugins"][0]["allow_tool_override_observed"] is False
    assert len(docker.exec_script_calls) == 1
    assert docker.exec_script_calls[0][1] == 30.0
    assert "/opt/hermes/bin/hermes" in docker.exec_script_calls[0][0]


def test_status_is_safe_when_container_is_stopped(tmp_path: Path) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()
    manager.apply()
    docker.running = False

    status = manager.status()

    assert status["observed"]["container_running"] is False
    assert status["observed"]["bundled_skill_manifest"] is None
    assert status["observed"]["essential_skills"] is None
    assert status["observed"]["live_observation_ok"] is False


def test_doctor_reports_stopped_container_without_live_checks(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()
    manager.apply()
    docker.running = False

    report = manager.doctor()

    assert not report.is_ready
    names = [check.name for check in report.checks]
    assert "plugin_sync:acmm-tools" in names
    assert "container_running" in names
    assert "model_provider" not in names
    assert "blank_slate_essential_skills" not in names
    check = next(
        item
        for item in report.checks
        if item.name == "container_running"
    )
    assert not check.ok
    assert "acmm-infra hermes up" in check.detail


def test_doctor_reports_failed_live_observation_once(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()
    manager.apply()
    docker.running = True
    docker.exec_script_result = command_result(
        args=("exec-script",),
        returncode=1,
        stdout="",
        stderr="live inspection failed",
    )

    report = manager.doctor()

    assert not report.is_ready
    names = [check.name for check in report.checks]
    assert "live_observation" in names
    assert "model_provider" not in names
    check = next(
        item
        for item in report.checks
        if item.name == "live_observation"
    )
    assert not check.ok
    assert check.detail == "live inspection failed"


def test_doctor_is_ready_for_matching_baseline(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    manager.initialize_deployment()
    manager.apply()
    manager.docker.running = True

    report = manager.doctor()

    assert report.is_ready
    assert all(check.ok for check in report.checks)


def test_doctor_detects_wrong_model(tmp_path: Path) -> None:
    manager, docker = _manager(tmp_path)
    manager.apply()
    docker.running = True
    docker.model_default = "different-model"

    report = manager.doctor()

    assert not report.is_ready
    check = next(item for item in report.checks if item.name == "model_default")
    assert not check.ok


def test_reset_refuses_while_container_is_running(tmp_path: Path) -> None:
    manager, docker = _manager(tmp_path)
    docker.running = True
    manager.init_runtime()

    with pytest.raises(infrastructure_error, match="container is running"):
        manager.reset("sessions")


def test_reset_removes_only_configured_scope(tmp_path: Path) -> None:
    manager, docker = _manager(tmp_path)
    docker.running = False
    manager.init_runtime()

    sessions = manager.data_root / "sessions"
    credentials = manager.data_root / ".env"
    sessions.mkdir()
    (sessions / "session.json").write_text("{}", encoding="utf-8")
    credentials.write_text("SECRET=value", encoding="utf-8")

    removed = manager.reset("sessions")

    assert removed == (sessions,)
    assert not sessions.exists()
    assert credentials.read_text(encoding="utf-8") == "SECRET=value"


def test_apply_refuses_while_gateway_is_running(
    tmp_path: Path,
) -> None:
    docker = fake_docker()
    docker.running = True
    manager, _ = _manager(tmp_path, docker)

    with pytest.raises(infrastructure_error, match="gateway is running"):
        manager.apply()

    assert docker.provision_calls == []


def test_apply_tools_reconciles_only_managed_tool_scope(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()

    docker.model_provider = "custom-stable"
    docker.model_default = "stable-model"
    docker.context_window = 12345
    docker.title_generation_enabled = True
    docker.background_review_enabled = True
    docker.tool_search_enabled = "auto"
    docker.plugins_enabled = []

    result = manager.apply_tools()

    assert result == {
        "plugins_synchronized": ["acmm-tools"],
        "tool_search_enabled": "off",
        "provisioning_operations": 2,
    }
    assert docker.model_provider == "custom-stable"
    assert docker.model_default == "stable-model"
    assert docker.context_window == 12345
    assert docker.title_generation_enabled is True
    assert docker.background_review_enabled is True
    assert docker.tool_search_enabled == "off"
    assert docker.plugins_enabled == ["acmm-tools"]

    assert len(docker.provision_calls) == 1
    script, timeout_seconds = docker.provision_calls[0]
    assert timeout_seconds == 120.0
    assert "tools.tool_search.enabled" in script
    assert "plugins enable acmm-tools --no-allow-tool-override" in script
    assert "model.provider" not in script
    assert "model.default" not in script
    assert "agent.reasoning_effort" not in script
    assert "terminal.backend" not in script
    assert "apply Blank Slate policy" not in script
    assert "skills opt-out" not in script
    assert "auxiliary.title_generation.enabled" not in script
    assert "auxiliary.background_review.enabled" not in script


def test_apply_tools_refuses_while_gateway_is_running(
    tmp_path: Path,
) -> None:
    docker = fake_docker()
    docker.running = True
    manager, _ = _manager(tmp_path, docker)

    with pytest.raises(infrastructure_error, match="gateway is running"):
        manager.apply_tools()

    assert docker.provision_calls == []


def test_apply_tools_reports_narrow_progress(
    tmp_path: Path,
) -> None:
    manager, _ = _manager(tmp_path)
    messages: list[str] = []

    manager.apply_tools(progress=messages.append)

    assert messages == [
        "[1/3] Verifying initialized Hermes deployment.",
        "[2/3] Replacing repository-managed Hermes tool plugins.",
        (
            "[3/3] Applying 2 managed-tool operation(s) in one "
            "provisioning container (timeout 120s)."
        ),
        "[Hermes 1/2] set tools.tool_search.enabled",
        "✓ Set tools.tool_search.enabled = off",
        "[Hermes] Provisioning batch complete.",
    ]


def test_doctor_detects_experiment_isolation_drift(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()
    manager.apply()
    docker.running = True
    docker.title_generation_enabled = True
    docker.background_review_enabled = True
    docker.tool_search_enabled = "auto"

    report = manager.doctor()

    assert not report.is_ready
    checks = {item.name: item for item in report.checks}
    assert not checks["experiment_title_generation"].ok
    assert not checks["experiment_background_review"].ok
    assert not checks["experiment_tool_search"].ok


def test_apply_timeout_is_reported_as_error(
    tmp_path: Path,
) -> None:
    docker = fake_docker()
    docker.provision_result = command_result(
        args=("provision",),
        returncode=124,
        stdout="",
        stderr="Command timed out after 5 seconds.",
        timed_out=True,
    )
    manager, _ = _manager(tmp_path, docker)

    with pytest.raises(docker_error, match="timed out"):
        manager.apply(timeout_seconds=5.0)

    assert len(docker.provision_calls) == 1
    assert docker.provision_calls[0][1] == 5.0


def test_apply_reports_progress_stages(
    tmp_path: Path,
) -> None:
    manager, _ = _manager(tmp_path)
    messages: list[str] = []

    manager.apply(progress=messages.append)

    assert messages == [
        "[1/4] Verifying initialized Hermes deployment.",
        "[2/4] Synchronizing repository-managed Hermes plugins.",
        (
            "[3/4] Applying 14 Hermes operations in one provisioning container "
            "(timeout 120s)."
        ),
        "[Hermes 1/14] set model.provider",
        "✓ Set model.provider = custom",
        "[Hermes] Provisioning batch complete.",
        "[4/4] Hermes infrastructure baseline applied successfully.",
    ]


def test_apply_quiet_mode_suppresses_progress(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    messages: list[str] = []

    manager.apply(
        progress=messages.append,
        output_mode="quiet",
    )

    assert messages == []
    assert docker.provision_output_modes == ["quiet"]


def test_apply_verbose_mode_uses_raw_provision_output(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    messages: list[str] = []

    manager.apply(
        progress=messages.append,
        output_mode="verbose",
    )

    assert docker.provision_output_modes == ["verbose"]
    assert messages == [
        "[1/4] Verifying initialized Hermes deployment.",
        "[2/4] Synchronizing repository-managed Hermes plugins.",
        (
            "[3/4] Applying 14 Hermes operations in one provisioning container "
            "(timeout 120s)."
        ),
        "[4/4] Hermes infrastructure baseline applied successfully.",
    ]


def test_apply_rejects_nonpositive_timeout(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)

    with pytest.raises(infrastructure_error, match="greater than zero"):
        manager.apply(timeout_seconds=0)

    assert docker.provision_calls == []

def test_adopt_data_copies_existing_baseline_without_removing_source(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path / "new")
    docker.running = False

    source = tmp_path / "old_data"
    source.mkdir()
    (source / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    (source / ".env").write_text("SECRET=value\n", encoding="utf-8")

    destination = manager.adopt_data(source)

    assert destination == manager.data_root
    assert (destination / "config.yaml").read_text(encoding="utf-8") == "model: {}\n"
    assert (destination / ".env").read_text(encoding="utf-8") == "SECRET=value\n"
    assert (source / ".env").is_file()


def test_adopt_data_refuses_nonempty_destination(tmp_path: Path) -> None:
    manager, docker = _manager(tmp_path / "new")
    docker.running = False
    manager.init_runtime()
    (manager.data_root / "config.yaml").write_text("existing", encoding="utf-8")

    source = tmp_path / "old_data"
    source.mkdir()
    (source / "config.yaml").write_text("source", encoding="utf-8")

    with pytest.raises(infrastructure_error, match="not empty"):
        manager.adopt_data(source)

def test_initialize_deployment_installs_ready_to_use_defaults(
    tmp_path: Path,
) -> None:
    manager, _ = _manager(tmp_path)

    result = manager.initialize_deployment()

    assert result["compose"] == "created"
    assert result["env"] == "created"

    compose = manager.deployment_root / "compose.yaml"
    env = manager.deployment_root / ".env"

    assert compose.read_bytes() == (
        ROOT
        / "infrastructure"
        / "hermes"
        / "defaults"
        / "compose.yaml"
    ).read_bytes()

    assert env.read_bytes() == (
        ROOT
        / "infrastructure"
        / "hermes"
        / "defaults"
        / "env.defaults"
    ).read_bytes()

    assert manager.data_root.is_dir()
    assert (manager.data_root / "plugins").is_dir()


def test_initialize_deployment_preserves_existing_local_files(
    tmp_path: Path,
) -> None:
    manager, _ = _manager(tmp_path)
    manager.deployment_root.mkdir(parents=True)

    compose = manager.deployment_root / "compose.yaml"
    env = manager.deployment_root / ".env"
    compose.write_text("custom compose\n", encoding="utf-8")
    env.write_text("LOCAL_SETTING=value\n", encoding="utf-8")

    result = manager.initialize_deployment()

    assert result["compose"] == "preserved"
    assert result["env"] == "preserved"
    assert compose.read_text(encoding="utf-8") == "custom compose\n"
    assert env.read_text(encoding="utf-8") == "LOCAL_SETTING=value\n"


def test_doctor_detects_deployed_compose_drift(
    tmp_path: Path,
) -> None:
    manager, _ = _manager(tmp_path)
    manager.initialize_deployment()
    manager.apply()
    manager.docker.running = True

    (manager.deployment_root / "compose.yaml").write_text(
        "services: {}\n",
        encoding="utf-8",
    )

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item
        for item in report.checks
        if item.name == "compose_default"
    )
    assert not check.ok

def test_init_creates_private_hermes_env_file(
    tmp_path: Path,
) -> None:
    manager, _ = _manager(tmp_path)

    manager.initialize_deployment()

    env_path = manager.data_root / ".env"

    assert env_path.is_file()
    assert env_path.read_text(encoding="utf-8") == ""
    assert (env_path.stat().st_mode & 0o777) == 0o600

def test_doctor_detects_wrong_provider_route(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()
    manager.apply()
    docker.running = True
    docker.model_base_url = "http://wrong-host:11434/v1"

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item
        for item in report.checks
        if item.name == "model_base_url"
    )
    assert not check.ok


def test_doctor_detects_wrong_context_window(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()
    manager.apply()
    docker.running = True
    docker.context_window = 32768

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item
        for item in report.checks
        if item.name == "model_context_length"
    )
    assert not check.ok

def test_doctor_detects_blank_slate_memory_drift(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()
    manager.apply()
    docker.running = True
    docker.memory_enabled = True

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item
        for item in report.checks
        if item.name == "blank_slate_memory"
    )
    assert not check.ok


def test_doctor_detects_blank_slate_toolset_drift(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()
    manager.apply()
    docker.running = True
    docker.platform_toolsets_cli.append("web")

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item
        for item in report.checks
        if item.name == "blank_slate_toolsets"
    )
    assert not check.ok


def test_doctor_detects_extra_bundled_skill(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()
    manager.apply()
    docker.running = True
    docker.bundled_skill_manifest.append("github")

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item
        for item in report.checks
        if item.name == "blank_slate_essential_skills"
    )
    assert not check.ok


def test_apply_rejects_legacy_config_before_starting_provisioner(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()
    (manager.data_root / "config.yaml").write_text(
        "_config_version: 11\nmodel: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(infrastructure_error, match="reset configuration"):
        manager.apply()

    assert docker.provision_calls == []


def test_apply_allows_unversioned_partial_config_for_retry(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()
    (manager.data_root / "config.yaml").write_text(
        "model:\n  provider: custom\n",
        encoding="utf-8",
    )

    result = manager.apply()

    assert result["blank_slate"] is True
    assert len(docker.provision_calls) == 1


def test_configuration_reset_removes_only_config_yaml(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    docker.running = False
    manager.initialize_deployment()

    config = manager.data_root / "config.yaml"
    env = manager.data_root / ".env"
    config.write_text("_config_version: 11\n", encoding="utf-8")
    env.write_text("KEEP=value\n", encoding="utf-8")

    removed = manager.reset("configuration")

    assert removed == (config,)
    assert not config.exists()
    assert env.read_text(encoding="utf-8") == "KEEP=value\n"


def test_doctor_detects_wrong_terminal_backend(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)
    manager.initialize_deployment()
    manager.apply()
    docker.running = True
    docker.terminal_backend = "docker"

    report = manager.doctor()

    assert not report.is_ready
    check = next(
        item
        for item in report.checks
        if item.name == "terminal_backend"
    )
    assert not check.ok

def test_blank_slate_python_runs_directly_in_already_unprivileged_shell(
    tmp_path: Path,
) -> None:
    manager, docker = _manager(tmp_path)

    manager.apply()

    script, _ = docker.provision_calls[0]

    assert "/command/s6-setuidgid" not in script
    assert script.count("/opt/hermes/.venv/bin/python") >= 2

