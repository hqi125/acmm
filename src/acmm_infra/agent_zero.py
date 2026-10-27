from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

from acmm_infra.agent_zero_api import agent_zero_api_client, agent_zero_api_error
from acmm_infra.common import doctor_check, doctor_report, infrastructure_error
from acmm_infra.deployment import (
    bool_text,
    clear_experiment_apparatus_state,
    experiment_application_scope,
    files_equal,
    install_default_if_missing,
    read_env,
    read_experiment_apparatus_state,
    write_env,
    write_experiment_apparatus_state,
)
from acmm_infra.docker import docker_client, require_success
from acmm_infra.profiles.agent_zero import agent_zero_profile, managed_agent_zero_plugin
from acmm_infra.profiles.experiments import (
    experiment_apparatus_sha256,
    registered_experiment_apparatus_ids,
    with_agent_zero_experiment,
)
from acmm_infra.source_tree import (
    copytree_ignore_generated,
    iter_managed_source_files,
)


_RETIRED_ENV_KEYS = (
    "AGENT_ZERO_CHAT_MODEL_PROVIDER",
    "AGENT_ZERO_CHAT_MODEL_NAME",
    "AGENT_ZERO_CHAT_MODEL_API_BASE",
    "AGENT_ZERO_CHAT_MODEL_CTX_LENGTH",
    "AGENT_ZERO_UTIL_MODEL_PROVIDER",
    "AGENT_ZERO_UTIL_MODEL_NAME",
    "AGENT_ZERO_UTIL_MODEL_API_BASE",
    "AGENT_ZERO_UTIL_MODEL_CTX_LENGTH",
)


class agent_zero_manager:
    def __init__(
        self,
        repo_root: str | Path,
        runtime_root: str | Path,
        profile: agent_zero_profile,
        docker: docker_client,
        api: agent_zero_api_client | None = None,
        experiment_id: str | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.profile = profile
        self.docker = docker
        self.experiment_id = experiment_id
        self.deployment_root = (
            self.runtime_root / profile.deployment_directory
        ).resolve()
        self.data_root = (self.deployment_root / profile.data_directory).resolve()
        self.defaults_root = (
            self.repo_root / "infrastructure" / "agent-zero" / "defaults"
        ).resolve()
        self.agent_zero_root = (
            self.repo_root / "infrastructure" / "agent-zero"
        ).resolve()
        self.api = api or agent_zero_api_client(
            f"http://127.0.0.1:{profile.web_port}",
            timeout_seconds=10.0,
        )

    def initialize_deployment(self) -> dict[str, str]:
        self.deployment_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)

        compose_state = install_default_if_missing(
            self.defaults_root / "compose.yaml",
            self.deployment_root / "compose.yaml",
        )
        env_state = install_default_if_missing(
            self.defaults_root / "env.defaults",
            self.deployment_root / ".env",
        )

        return {
            "compose": compose_state,
            "env": env_state,
            "deployment_root": str(self.deployment_root),
            "data_root": str(self.data_root),
        }

    def up(self, *, timeout_seconds: float = 120.0) -> None:
        if timeout_seconds <= 0:
            raise infrastructure_error(
                "Agent Zero startup timeout must be greater than zero seconds."
            )

        self.initialize_deployment()
        require_success(self.docker.compose_config(), "Docker Compose validation")
        require_success(self.docker.up(), "Starting Agent Zero infrastructure")
        self._wait_until_ready(timeout_seconds)

    def down(self) -> None:
        require_success(self.docker.down(), "Stopping Agent Zero infrastructure")

    def apply(
        self,
        *,
        progress: Callable[[str], None] | None = None,
        timeout_seconds: float = 120.0,
    ) -> dict[str, Any]:
        if timeout_seconds <= 0:
            raise infrastructure_error(
                "Agent Zero provisioning timeout must be greater than zero seconds."
            )

        report = progress if progress is not None else lambda message: None
        experiment_id = self.experiment_id
        report("[1/5] Reconciling Agent Zero deployment files and environment.")
        self.initialize_deployment()

        if self.docker.container_running():
            raise infrastructure_error(
                "Agent Zero is running. Run 'acmm-infra agent-zero down' before apply."
            )

        self._reconcile_deployment()
        require_success(self.docker.compose_config(), "Docker Compose validation")

        report("[2/5] Starting one temporary Agent Zero provisioning service.")
        require_success(self.docker.up(), "Starting Agent Zero provisioning service")
        synchronized: tuple[str, ...] = ()
        file_state: dict[str, str] = {}
        model_state = "unknown"
        settings_state = "unknown"
        live_state: dict[str, Any] | None = None
        try:
            self._wait_until_ready(timeout_seconds)

            if experiment_id is None:
                report("[3/5] Reconciling baseline profile files and Tool policy.")
            else:
                report(
                    "[3/5] Reconciling Agent Zero apparatus for experiment "
                    f"'{experiment_id}'."
                )
            desired_destinations = {
                plugin.destination.as_posix() for plugin in self.profile.plugins
            }
            self._remove_stale_experiment_plugins(
                desired_destinations=desired_destinations
            )
            synchronized = self._reconcile_plugins_live()
            file_state = self._reconcile_application_files_live()

            report("[4/5] Reconciling model preset and persistent settings.")
            model_state = self._reconcile_model_preset()
            settings_state = self._reconcile_persistent_settings()
            live_state = self._observe_live_application()
        finally:
            require_success(self.docker.down(), "Stopping Agent Zero provisioning service")

        if experiment_id is None:
            report("[5/5] Agent Zero baseline reconciled and verified.")
        else:
            report(
                "[5/5] Agent Zero experiment environment reconciled and verified: "
                f"{experiment_id}."
            )
        if live_state is None or live_state.get("error"):
            detail = "unknown application verification failure"
            if live_state is not None:
                detail = str(live_state.get("error") or detail)
            raise infrastructure_error(
                "Agent Zero application state could not be verified: " + detail
            )
        if not live_state["settings_exact"]:
            raise infrastructure_error(
                "Agent Zero persistent settings do not match the ACMM profile after apply."
            )
        if not live_state["model_config_exact"]:
            raise infrastructure_error(
                "Agent Zero model preset/profile selection does not match the ACMM profile."
            )

        source_sha256: str | None = None
        if experiment_id is None:
            clear_experiment_apparatus_state(self.deployment_root)
        else:
            source_sha256 = experiment_apparatus_sha256(
                self.repo_root,
                "agent-zero",
                experiment_id,
            )
            write_experiment_apparatus_state(
                self.deployment_root,
                experiment_id=experiment_id,
                source_sha256=source_sha256,
                application_scope="experiment",
                resources=[
                    {
                        "kind": "plugin",
                        "name": plugin.name,
                        "destination": plugin.destination.as_posix(),
                    }
                    for plugin in self.profile.plugins
                ],
            )

        return {
            "image": self.profile.image,
            "agent_profile": self.profile.agent_profile,
            "model_preset": self.profile.model_preset,
            "model_provider": self.profile.model_provider,
            "model_base_url": self.profile.model_base_url,
            "model_default": self.profile.model_default,
            "context_window": self.profile.context_window,
            "utility_model_provider": self.profile.utility_model_provider,
            "utility_model_base_url": self.profile.utility_model_base_url,
            "utility_model_default": self.profile.utility_model_default,
            "utility_context_window": self.profile.utility_context_window,
            "plugins_synchronized": synchronized,
            "application_files": file_state,
            "model_preset_state": model_state,
            "persistent_settings": settings_state,
            "provisioning_service": "stopped after apply",
            "experiment_id": experiment_id,
            "source_sha256": source_sha256,
            "application_scope": (
                "experiment" if experiment_id is not None else None
            ),
        }

    def apply_experiment(
        self,
        experiment_id: str,
        *,
        application_scope: experiment_application_scope = "experiment",
        timeout_seconds: float = 120.0,
    ) -> dict[str, Any]:
        if not self.profile.plugins:
            raise infrastructure_error(
                "Agent Zero experiment profile contains no managed apparatus plugins."
            )
        if timeout_seconds <= 0:
            raise infrastructure_error(
                "Agent Zero apply-experiment timeout must be greater than zero seconds."
            )

        self.initialize_deployment()
        if self.docker.container_running():
            raise infrastructure_error(
                "Agent Zero is running. Run 'acmm-infra agent-zero down' before "
                "applying experiment apparatus."
            )

        require_success(self.docker.compose_config(), "Docker Compose validation")
        require_success(
            self.docker.up(),
            "Starting Agent Zero experiment provisioning service",
        )
        try:
            self._wait_until_ready(timeout_seconds)
            desired_destinations = {
                plugin.destination.as_posix() for plugin in self.profile.plugins
            }
            self._remove_stale_experiment_plugins(
                desired_destinations=desired_destinations
            )
            synchronized = self._reconcile_plugins_live()
            self._reconcile_application_files_live()
        finally:
            require_success(
                self.docker.down(),
                "Stopping Agent Zero experiment provisioning service",
            )

        source_sha256 = experiment_apparatus_sha256(
            self.repo_root,
            "agent-zero",
            experiment_id,
        )
        write_experiment_apparatus_state(
            self.deployment_root,
            experiment_id=experiment_id,
            source_sha256=source_sha256,
            application_scope=application_scope,
            resources=[
                {
                    "kind": "plugin",
                    "name": plugin.name,
                    "destination": plugin.destination.as_posix(),
                }
                for plugin in self.profile.plugins
            ],
        )
        return {
            "experiment_id": experiment_id,
            "source_sha256": source_sha256,
            "application_scope": application_scope,
            "plugins_synchronized": synchronized,
        }

    def apply_tools(
        self,
        experiment_id: str | None,
        *,
        timeout_seconds: float = 120.0,
    ) -> dict[str, Any]:
        if experiment_id is not None:
            if not self.profile.plugins:
                raise infrastructure_error(
                    "Agent Zero experiment profile contains no managed apparatus plugins."
                )
            return self._apply_tool_apparatus(
                experiment_id=experiment_id,
                timeout_seconds=timeout_seconds,
            )

        if timeout_seconds <= 0:
            raise infrastructure_error(
                "Agent Zero apply-tools timeout must be greater than zero seconds."
            )

        self.initialize_deployment()
        if self.docker.container_running():
            raise infrastructure_error(
                "Agent Zero is running. Run 'acmm-infra agent-zero down' before "
                "applying baseline tool policy."
            )

        require_success(self.docker.compose_config(), "Docker Compose validation")
        require_success(
            self.docker.up(),
            "Starting Agent Zero tool-policy provisioning service",
        )
        try:
            self._wait_until_ready(timeout_seconds)
            self._remove_stale_experiment_plugins(desired_destinations=set())
            synchronized = self._reconcile_plugins_live()
            file_state = self._reconcile_tool_policy_live()
        finally:
            require_success(
                self.docker.down(),
                "Stopping Agent Zero tool-policy provisioning service",
            )

        clear_experiment_apparatus_state(self.deployment_root)
        return {
            "experiment_id": None,
            "application_scope": "tools",
            "plugins_synchronized": synchronized,
            "application_files": file_state,
        }

    def _apply_tool_apparatus(
        self,
        *,
        experiment_id: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        if timeout_seconds <= 0:
            raise infrastructure_error(
                "Agent Zero apply-tools timeout must be greater than zero seconds."
            )

        self.initialize_deployment()
        if self.docker.container_running():
            raise infrastructure_error(
                "Agent Zero is running. Run 'acmm-infra agent-zero down' before "
                "applying experiment tool apparatus."
            )

        require_success(self.docker.compose_config(), "Docker Compose validation")
        require_success(
            self.docker.up(),
            "Starting Agent Zero tool-policy provisioning service",
        )
        try:
            self._wait_until_ready(timeout_seconds)
            desired_destinations = {
                plugin.destination.as_posix() for plugin in self.profile.plugins
            }
            self._remove_stale_experiment_plugins(
                desired_destinations=desired_destinations
            )
            synchronized = self._reconcile_plugins_live()
            file_state = self._reconcile_tool_policy_live()
        finally:
            require_success(
                self.docker.down(),
                "Stopping Agent Zero tool-policy provisioning service",
            )

        source_sha256 = experiment_apparatus_sha256(
            self.repo_root,
            "agent-zero",
            experiment_id,
        )
        write_experiment_apparatus_state(
            self.deployment_root,
            experiment_id=experiment_id,
            source_sha256=source_sha256,
            application_scope="tools",
            resources=[
                {
                    "kind": "plugin",
                    "name": plugin.name,
                    "destination": plugin.destination.as_posix(),
                }
                for plugin in self.profile.plugins
            ],
        )
        return {
            "experiment_id": experiment_id,
            "source_sha256": source_sha256,
            "application_scope": "tools",
            "plugins_synchronized": synchronized,
            "application_files": file_state,
        }

    def status(self) -> dict[str, Any]:
        running, image = self.docker.container_info()
        compose_path = self.deployment_root / "compose.yaml"
        env_path = self.deployment_root / ".env"
        default_compose = self.defaults_root / "compose.yaml"
        observed_version = None

        if running:
            result = self.docker.exec_script(
                "git -C /a0 describe --tags --exact-match HEAD 2>/dev/null "
                "|| git -C /a0 describe --tags --abbrev=0 2>/dev/null"
            )
            if result.succeeded:
                observed_version = result.stdout.strip() or None

        env_values = read_env(env_path) if env_path.is_file() else {}
        managed_plugins = {
            plugin.name: self._plugin_status(plugin)
            for plugin in self.profile.plugins
        }
        application_files = self._application_file_status()
        live_application = self._observe_live_application() if running else None

        return {
            "runtime_root": str(self.runtime_root),
            "deployment_root": str(self.deployment_root),
            "experiment_apparatus": read_experiment_apparatus_state(
                self.deployment_root
            ),
            "data_root": str(self.data_root),
            "deployment": {
                "compose_path": str(compose_path),
                "compose_matches_default": files_equal(compose_path, default_compose),
                "env_path": str(env_path),
            },
            "desired": {
                "container_name": self.profile.container_name,
                "image": self.profile.image,
                "version": self.profile.expected_version,
                "web_port": self.profile.web_port,
                "agent_profile": self.profile.agent_profile,
                "model_preset": self.profile.model_preset,
                "model_provider": self.profile.model_provider,
                "model_base_url": self.profile.model_base_url,
                "model_default": self.profile.model_default,
                "context_window": self.profile.context_window,
                "utility_model_provider": self.profile.utility_model_provider,
                "utility_model_base_url": self.profile.utility_model_base_url,
                "utility_model_default": self.profile.utility_model_default,
                "utility_context_window": self.profile.utility_context_window,
                "managed_plugins": tuple(plugin.name for plugin in self.profile.plugins),
            },
            "observed": {
                "container_running": running,
                "container_image": image,
                "version": observed_version,
                "managed_env": {
                    key: env_values.get(key)
                    for key in self._managed_env()
                },
                "managed_plugins": managed_plugins,
                "application_files": application_files,
                "live_application": live_application,
            },
        }

    def doctor(self) -> doctor_report:
        status = self.status()
        desired = status["desired"]
        observed = status["observed"]
        deployment = status["deployment"]
        checks: list[doctor_check] = []

        marker = status["experiment_apparatus"]
        if self.experiment_id is None:
            apparatus_ok = marker is None
            apparatus_detail = (
                "none"
                if marker is None
                else f"active={marker.get('experiment_id')!r}"
            )
        else:
            expected_apparatus_hash = experiment_apparatus_sha256(
                self.repo_root,
                "agent-zero",
                self.experiment_id,
            )
            apparatus_ok = (
                isinstance(marker, dict)
                and marker.get("experiment_id") == self.experiment_id
                and marker.get("source_sha256") == expected_apparatus_hash
            )
            apparatus_detail = (
                f"observed_id={marker.get('experiment_id')!r}, "
                f"observed_sha256={marker.get('source_sha256')!r}, "
                f"expected_id={self.experiment_id!r}, "
                f"expected_sha256={expected_apparatus_hash!r}"
                if isinstance(marker, dict)
                else (
                    f"observed=None, expected_id={self.experiment_id!r}, "
                    f"expected_sha256={expected_apparatus_hash!r}"
                )
            )
        checks.append(
            doctor_check(
                "experiment_apparatus_identity",
                apparatus_ok,
                apparatus_detail,
            )
        )

        docker_version = self.docker.docker_version()
        checks.append(
            doctor_check(
                "docker",
                docker_version.succeeded,
                docker_version.stdout.strip() or docker_version.stderr.strip(),
            )
        )
        compose_version = self.docker.compose_version()
        checks.append(
            doctor_check(
                "docker_compose",
                compose_version.succeeded,
                compose_version.stdout.strip() or compose_version.stderr.strip(),
            )
        )

        compose_ok = deployment["compose_matches_default"]
        checks.append(
            doctor_check(
                "compose_default",
                compose_ok,
                "matches repository default" if compose_ok else "deployment compose drifted",
            )
        )

        expected_env = self._managed_env()
        observed_env = observed["managed_env"]
        env_ok = all(observed_env.get(key) == value for key, value in expected_env.items())
        checks.append(
            doctor_check(
                "managed_environment",
                env_ok,
                "matches profile" if env_ok else "managed deployment environment drifted",
            )
        )

        for name, plugin_state in observed["managed_plugins"].items():
            checks.append(
                doctor_check(
                    f"managed_plugin:{name}",
                    plugin_state["exact"],
                    (
                        "source-owned files match repository source"
                        if plugin_state["exact"]
                        else (
                            "is missing"
                            if not plugin_state["present"]
                            else "source-owned files drifted from repository source"
                        )
                    ),
                )
            )

        application_files = observed["application_files"]
        application_files_ok = all(
            item["exact"] for item in application_files.values()
        )
        checks.append(
            doctor_check(
                "application_baseline_files",
                application_files_ok,
                (
                    "profile, local-model prompts, model preset, and isolation files match profile"
                    if application_files_ok
                    else "one or more managed Agent Zero application files drifted"
                ),
            )
        )

        running = observed["container_running"]
        checks.append(
            doctor_check(
                "container_running",
                running,
                "running" if running else "Agent Zero container is stopped",
            )
        )

        if running:
            image_ok = observed["container_image"] == desired["image"]
            checks.append(
                doctor_check(
                    "container_image",
                    image_ok,
                    f"observed {observed['container_image']!r}; expected {desired['image']!r}",
                )
            )

            version = observed["version"]
            version_ok = version == desired["version"]
            checks.append(
                doctor_check(
                    "agent_zero_version",
                    version_ok,
                    f"observed {version!r}; expected {desired['version']!r}",
                )
            )

            ready, detail = self._probe_ready()
            checks.append(doctor_check("web_backend", ready, detail))

            live = observed["live_application"]
            if isinstance(live, dict) and not live.get("error"):
                checks.append(
                    doctor_check(
                        "persistent_settings",
                        bool(live["settings_exact"]),
                        (
                            "managed Agent Zero settings match profile"
                            if live["settings_exact"]
                            else "managed Agent Zero settings drifted"
                        ),
                    )
                )
                checks.append(
                    doctor_check(
                        "model_config",
                        bool(live["model_config_exact"]),
                        (
                            "effective ACMM profile model configuration matches profile"
                            if live["model_config_exact"]
                            else "effective ACMM profile model configuration drifted"
                        ),
                    )
                )
                checks.append(
                    doctor_check(
                        "experiment_model",
                        bool(live["model_available"]),
                        (
                            f"model {self.profile.model_default!r} is available"
                            if live["model_available"]
                            else live["model_availability_detail"]
                        ),
                    )
                )
            else:
                detail = "Agent Zero application API could not be inspected"
                if isinstance(live, dict) and live.get("error"):
                    detail += f": {live['error']}"
                checks.append(doctor_check("persistent_settings", False, detail))
                checks.append(doctor_check("model_config", False, detail))
                checks.append(doctor_check("experiment_model", False, detail))

        return doctor_report(tuple(checks))

    def reset(self, scope: str) -> tuple[Path, ...]:
        if self.docker.container_running():
            raise infrastructure_error(
                "Agent Zero is running. Run 'acmm-infra agent-zero down' before reset."
            )

        paths = self.profile.reset_scopes.get(scope)
        if paths is None:
            available = ", ".join(sorted(self.profile.reset_scopes))
            raise infrastructure_error(
                f"Unknown reset scope '{scope}'. Available scopes: {available}."
            )

        removed: list[Path] = []
        for relative in paths:
            target = (self.data_root / relative).resolve()
            if not target.is_relative_to(self.data_root):
                raise infrastructure_error("Refusing reset outside Agent Zero data root.")
            if not target.exists() and not target.is_symlink():
                continue
            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)
            removed.append(target)

        return tuple(removed)

    def _reconcile_deployment(self) -> None:
        shutil.copy2(
            self.defaults_root / "compose.yaml",
            self.deployment_root / "compose.yaml",
        )
        env_path = self.deployment_root / ".env"
        values = read_env(env_path)
        for key in _RETIRED_ENV_KEYS:
            values.pop(key, None)
        values.update(self._managed_env())
        write_env(
            env_path,
            values,
            header_lines=(
                "# ACMM Agent Zero deployment configuration.",
                "# Managed keys are reconciled by 'acmm-infra agent-zero apply'.",
            ),
        )

    def _reconcile_plugins(self) -> tuple[str, ...]:
        synchronized: list[str] = []
        for plugin in self.profile.plugins:
            self._sync_plugin(plugin)
            synchronized.append(plugin.name)
        return tuple(synchronized)

    def _sync_plugin(self, plugin: managed_agent_zero_plugin) -> None:
        source = (self.agent_zero_root / plugin.source).resolve()
        destination = (self.data_root / plugin.destination).resolve()

        if not source.is_relative_to(self.agent_zero_root) or not source.is_dir():
            raise infrastructure_error(
                f"Agent Zero managed plugin source is invalid: '{source}'."
            )
        if not destination.is_relative_to(self.data_root):
            raise infrastructure_error(
                f"Refusing Agent Zero plugin destination outside data root: '{destination}'."
            )

        if destination.exists() or destination.is_symlink():
            if destination.is_symlink() or destination.is_file():
                destination.unlink()
            else:
                shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source,
            destination,
            ignore=copytree_ignore_generated,
        )

    def _plugin_status(self, plugin: managed_agent_zero_plugin) -> dict[str, Any]:
        source = (self.agent_zero_root / plugin.source).resolve()
        destination = (self.data_root / plugin.destination).resolve()
        present = destination.is_dir()
        return {
            "present": present,
            "exact": present and _directories_equal(source, destination),
            "path": str(destination),
        }

    def _managed_env(self) -> dict[str, str]:
        isolation = self.profile.experiment_isolation
        return {
            "AGENT_ZERO_IMAGE": self.profile.image,
            "AGENT_ZERO_CONTAINER_NAME": self.profile.container_name,
            "AGENT_ZERO_PORT": str(self.profile.web_port),
            "AGENT_ZERO_AGENT_PROFILE": self.profile.agent_profile,
            "AGENT_ZERO_MCP_SERVERS": '{"mcpServers":{}}',
            "AGENT_ZERO_MCP_SERVER_ENABLED": bool_text(isolation.mcp_server_enabled),
            "AGENT_ZERO_A2A_SERVER_ENABLED": bool_text(isolation.a2a_server_enabled),
            "AGENT_ZERO_UPDATE_CHECK_ENABLED": bool_text(isolation.update_check_enabled),
            "AGENT_ZERO_CHAT_INHERIT_PROJECT": bool_text(isolation.chat_inherit_project),
            "AGENT_ZERO_RFC_AUTO_DOCKER": bool_text(isolation.rfc_auto_docker),
        }

    def _managed_settings(self) -> dict[str, Any]:
        isolation = self.profile.experiment_isolation
        return {
            "agent_profile": self.profile.agent_profile,
            "mcp_servers": '{"mcpServers":{}}',
            "mcp_server_enabled": isolation.mcp_server_enabled,
            "a2a_server_enabled": isolation.a2a_server_enabled,
            "update_check_enabled": isolation.update_check_enabled,
            "chat_inherit_project": isolation.chat_inherit_project,
            "rfc_auto_docker": isolation.rfc_auto_docker,
        }

    def _reconcile_persistent_settings(self) -> str:
        client = self.api
        response = client.get_settings()
        settings = response.get("settings")
        if not isinstance(settings, dict):
            raise infrastructure_error(
                "Agent Zero settings response did not contain a settings object."
            )

        desired = self._managed_settings()
        changed = any(settings.get(key) != value for key, value in desired.items())
        settings_file_exists = (self.data_root / "settings.json").is_file()
        if not changed and settings_file_exists:
            return "unchanged"

        updated = client.update_settings(desired)
        updated_settings = updated.get("settings")
        if not isinstance(updated_settings, dict):
            raise infrastructure_error(
                "Agent Zero settings update did not return a settings object."
            )
        if any(updated_settings.get(key) != value for key, value in desired.items()):
            raise infrastructure_error(
                "Agent Zero settings update did not persist the managed baseline."
            )
        return "updated" if settings_file_exists else "created"

    def _model_preset_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "name": self.profile.model_preset,
                "chat": {
                    "provider": self.profile.model_provider,
                    "name": self.profile.model_default,
                    "api_base": self.profile.model_base_url,
                    "ctx_length": self.profile.context_window,
                    "ctx_history": 0.7,
                    "vision": False,
                    "max_embeds": 10,
                    "rl_requests": 0,
                    "rl_input": 0,
                    "rl_output": 0,
                    "kwargs": {},
                },
                "utility": {
                    "provider": self.profile.utility_model_provider,
                    "name": self.profile.utility_model_default,
                    "api_base": self.profile.utility_model_base_url,
                    "ctx_length": self.profile.utility_context_window,
                    "ctx_input": 0.7,
                    "rl_requests": 0,
                    "rl_input": 0,
                    "rl_output": 0,
                    "kwargs": {},
                },
                "embedding": {
                    "provider": self.profile.embedding_model_provider,
                    "name": self.profile.embedding_model_default,
                    "api_base": "",
                    "rl_requests": 0,
                    "rl_input": 0,
                    "kwargs": {},
                },
            }
        ]

    def _application_file_payloads(self) -> dict[Path, bytes]:
        isolation = self.profile.experiment_isolation
        profile_root = Path("agents") / self.profile.agent_profile

        agent_yaml = {
            "title": "ACMM",
            "description": "Controlled Agent Zero profile for ACMM experiments.",
            "context": "Use this profile for ACMM compliance experiments.",
        }
        tool_policy = {
            "mode": "custom",
            "default": isolation.tool_default,
            "mcp_default": isolation.mcp_tool_default,
            "allowed": list(isolation.allowed_tools),
            "blocked": [],
        }
        skills = {
            "visibility_policy": {
                "mode": "custom",
                "default": isolation.skills_default,
                "allowed": [],
                "blocked": [],
            }
        }
        memory = {
            "memory_recall_enabled": isolation.memory_recall_enabled,
            "memory_memorize_enabled": isolation.memory_memorize_enabled,
            "memory_memorize_consolidation": isolation.memory_memorize_consolidation,
        }
        chat_naming = {
            "automatic_naming": isolation.automatic_chat_naming,
            "automatic_naming_mode": "once",
        }
        prompt_root = self.agent_zero_root / "profile-prompts"

        return {
            profile_root / "agent.yaml": _yaml_bytes(agent_yaml),
            profile_root / "plugins/_tool_access/config.json": _json_bytes(tool_policy),
            profile_root / "plugins/_skills/config.json": _json_bytes(skills),
            profile_root / "plugins/_memory/config.json": _json_bytes(memory),
            profile_root / "plugins/_chat_naming/config.json": _json_bytes(chat_naming),
            profile_root / "prompts/agent.system.main.communication.md": (
                prompt_root / "agent.system.main.communication.md"
            ).read_bytes(),
            profile_root / "prompts/agent.system.main.solving.md": (
                prompt_root / "agent.system.main.solving.md"
            ).read_bytes(),
            profile_root / "prompts/agent.system.tools.md": (
                prompt_root / "agent.system.tools.md"
            ).read_bytes(),
            profile_root / "prompts/agent.system.tool.response.md": (
                prompt_root / "agent.system.tool.response.md"
            ).read_bytes(),
            profile_root / "prompts/fw.msg_repeat.md": (
                prompt_root / "fw.msg_repeat.md"
            ).read_bytes(),
            profile_root / "prompts/fw.initial_user_message.md": (
                prompt_root / "fw.initial_user_message.md"
            ).read_bytes(),
            profile_root / "prompts/fw.initial_message.md": (
                prompt_root / "fw.initial_message.md"
            ).read_bytes(),
        }

    def _remove_stale_experiment_plugins(
        self,
        *,
        desired_destinations: set[str],
    ) -> None:
        candidates: set[str] = set()
        state = read_experiment_apparatus_state(self.deployment_root)
        if state is not None:
            resources = state.get("resources")
            if isinstance(resources, list):
                for resource in resources:
                    if (
                        isinstance(resource, dict)
                        and resource.get("kind") == "plugin"
                        and isinstance(resource.get("destination"), str)
                    ):
                        candidates.add(resource["destination"])

        for experiment_id in registered_experiment_apparatus_ids(
            self.repo_root,
            "agent-zero",
        ):
            profile = with_agent_zero_experiment(
                self.repo_root,
                self.profile,
                experiment_id,
            )
            candidates.update(
                plugin.destination.as_posix() for plugin in profile.plugins
            )

        for destination in sorted(candidates - desired_destinations):
            require_success(
                self.docker.replace_container_tree(
                    f"/a0/usr/{destination}",
                    {},
                ),
                f"Removing stale Agent Zero experiment plugin '{destination}'",
            )

    def _reconcile_plugins_live(self) -> tuple[str, ...]:
        synchronized: list[str] = []
        for plugin in self.profile.plugins:
            source = (self.agent_zero_root / plugin.source).resolve()
            if not source.is_relative_to(self.agent_zero_root) or not source.is_dir():
                raise infrastructure_error(
                    f"Agent Zero managed plugin source is invalid: '{source}'."
                )

            files = {
                str(path.relative_to(source)).replace("\\", "/"): path.read_bytes()
                for path in iter_managed_source_files(source)
            }
            destination = f"/a0/usr/{plugin.destination.as_posix()}"
            require_success(
                self.docker.replace_container_tree(destination, files),
                f"Synchronizing Agent Zero managed plugin '{plugin.name}'",
            )
            synchronized.append(plugin.name)
        return tuple(synchronized)

    def _reconcile_application_files_live(self) -> dict[str, str]:
        payloads = self._application_file_payloads()
        profile_root = Path("agents") / self.profile.agent_profile
        relative_files: dict[str, bytes] = {}
        states: dict[str, str] = {}

        for relative, payload in payloads.items():
            target = (self.data_root / relative).resolve()
            if not target.is_relative_to(self.data_root):
                raise infrastructure_error(
                    f"Refusing Agent Zero application file outside data root: '{target}'."
                )
            previous = target.read_bytes() if target.is_file() else None
            states[str(relative)] = (
                "created"
                if previous is None
                else "unchanged"
                if previous == payload
                else "updated"
            )
            relative_files[str(relative.relative_to(profile_root)).replace("\\", "/")] = payload

        destination = f"/a0/usr/{profile_root.as_posix()}"
        require_success(
            self.docker.replace_container_tree(destination, relative_files),
            "Reconciling Agent Zero ACMM profile files",
        )
        return states

    def _reconcile_tool_policy_live(self) -> dict[str, str]:
        profile_root = Path("agents") / self.profile.agent_profile
        relative = profile_root / "plugins/_tool_access/config.json"
        payload = self._application_file_payloads()[relative]
        target = (self.data_root / relative).resolve()
        if not target.is_relative_to(self.data_root):
            raise infrastructure_error(
                f"Refusing Agent Zero application file outside data root: '{target}'."
            )

        previous = target.read_bytes() if target.is_file() else None
        state = (
            "created"
            if previous is None
            else "unchanged"
            if previous == payload
            else "updated"
        )
        require_success(
            self.docker.write_container_files(
                f"/a0/usr/{profile_root.as_posix()}",
                {"plugins/_tool_access/config.json": payload},
            ),
            "Reconciling Agent Zero Tool policy",
        )
        return {str(relative): state}

    def _reconcile_model_preset(self) -> str:
        client = self.api
        desired = self._model_preset_payload()
        observed = client.get_model_presets(agent_profile=self.profile.agent_profile)
        presets = observed.get("presets")
        configured = observed.get("configured_preset")
        selected = observed.get("selected_preset")
        changed = presets != desired

        if changed:
            saved = client.save_model_presets(desired)
            if saved.get("presets") != desired:
                raise infrastructure_error(
                    "Agent Zero model preset API did not persist the ACMM preset baseline."
                )

        if (
            changed
            or configured != self.profile.model_preset
            or selected != self.profile.model_preset
        ):
            selected_response = client.select_model_preset(
                agent_profile=self.profile.agent_profile,
                name=self.profile.model_preset,
            )
            if selected_response.get("selected_preset") != self.profile.model_preset:
                raise infrastructure_error(
                    "Agent Zero model preset API did not select the ACMM preset."
                )
            return "updated"

        return "unchanged"

    def _application_file_status(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for relative, payload in self._application_file_payloads().items():
            target = (self.data_root / relative).resolve()
            present = target.is_file()
            result[str(relative)] = {
                "present": present,
                "exact": present and target.read_bytes() == payload,
                "path": str(target),
            }
        return result

    def _observe_live_application(self) -> dict[str, Any]:
        try:
            client = self.api
            settings_response = client.get_settings()
            settings = settings_response.get("settings")
            if not isinstance(settings, dict):
                raise infrastructure_error(
                    "Agent Zero settings response did not contain a settings object."
                )

            desired_settings = self._managed_settings()
            settings_exact = all(
                settings.get(key) == value for key, value in desired_settings.items()
            )

            model_response = client.get_model_config(
                agent_profile=self.profile.agent_profile
            )
            model_config_exact = self._model_config_matches(model_response)

            search = client.search_models(
                provider=self.profile.model_provider,
                model_type="chat",
                api_base=self.profile.model_base_url,
                query=self.profile.model_default,
            )
            models = search.get("models")
            model_names = [str(value) for value in models] if isinstance(models, list) else []
            model_available = self.profile.model_default in model_names
            source = str(search.get("source") or "none")
            error = str(search.get("error") or "")
            if model_available:
                availability_detail = (
                    f"model {self.profile.model_default!r} is available via {source}"
                )
            elif error:
                availability_detail = (
                    f"model {self.profile.model_default!r} is not available; {error}"
                )
            else:
                availability_detail = (
                    f"model {self.profile.model_default!r} is not available; "
                    f"Agent Zero reported {len(model_names)} matching models"
                )

            return {
                "settings_exact": settings_exact,
                "settings": {
                    key: settings.get(key) for key in desired_settings
                },
                "model_config_exact": model_config_exact,
                "configured_preset": model_response.get("configured_preset"),
                "selected_preset": model_response.get("selected_preset"),
                "model_label": model_response.get("model_configured_label"),
                "model_available": model_available,
                "models_observed": model_names,
                "model_availability_detail": availability_detail,
                "error": None,
            }
        except (agent_zero_api_error, infrastructure_error) as exc:
            return {
                "settings_exact": False,
                "model_config_exact": False,
                "model_available": False,
                "models_observed": [],
                "model_availability_detail": str(exc),
                "error": str(exc),
            }

    def _model_config_matches(self, response: dict[str, Any]) -> bool:
        if response.get("configured_preset") != self.profile.model_preset:
            return False
        if response.get("selected_preset") != self.profile.model_preset:
            return False
        config = response.get("config")
        if not isinstance(config, dict):
            return False
        chat = config.get("chat_model")
        utility = config.get("utility_model")
        embedding = config.get("embedding_model")
        if not isinstance(chat, dict) or not isinstance(utility, dict):
            return False
        if not isinstance(embedding, dict):
            return False
        return (
            chat.get("provider") == self.profile.model_provider
            and chat.get("name") == self.profile.model_default
            and chat.get("api_base") == self.profile.model_base_url
            and chat.get("ctx_length") == self.profile.context_window
            and utility.get("provider") == self.profile.utility_model_provider
            and utility.get("name") == self.profile.utility_model_default
            and utility.get("api_base") == self.profile.utility_model_base_url
            and utility.get("ctx_length") == self.profile.utility_context_window
            and embedding.get("provider") == self.profile.embedding_model_provider
            and embedding.get("name") == self.profile.embedding_model_default
        )

    def _wait_until_ready(self, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_detail = "web backend did not respond"

        while True:
            if not self.docker.container_running():
                raise infrastructure_error(
                    "Agent Zero container stopped before the web backend became ready."
                )

            ready, detail = self._probe_ready()
            if ready:
                return
            last_detail = detail

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise infrastructure_error(
                    "Timed out waiting for Agent Zero web backend: " + last_detail
                )

            time.sleep(min(0.5, remaining))

    def _probe_ready(self) -> tuple[bool, str]:
        request = Request(
            f"http://127.0.0.1:{self.profile.web_port}/",
            headers={"Accept": "text/html,application/json"},
        )
        try:
            with urlopen(request, timeout=2.0) as response:
                status = int(response.status)
        except HTTPError as exc:
            status = exc.code
        except (OSError, URLError) as exc:
            return False, str(exc)

        if 200 <= status < 400 or status in {401, 403}:
            return True, f"HTTP {status}"
        return False, f"HTTP {status}"

_AGENT_ZERO_RUNTIME_PLUGIN_FILE_NAMES = frozenset(
    {
        "config.json",
        ".toggle-0",
        ".toggle-1",
    }
)
_AGENT_ZERO_RUNTIME_PLUGIN_DIRECTORY_NAMES = frozenset({"data"})


def _directories_equal(left: Path, right: Path) -> bool:
    if not left.is_dir() or not right.is_dir():
        return False

    left_files = _plugin_source_relative_files(left)
    right_files = _plugin_source_relative_files(right)
    if left_files != right_files:
        return False

    return all(
        (left / relative).read_bytes() == (right / relative).read_bytes()
        for relative in left_files
    )


def _plugin_source_relative_files(root: Path) -> list[Path]:
    return [
        path.relative_to(root)
        for path in iter_managed_source_files(root)
        if not _is_agent_zero_runtime_plugin_state(path.relative_to(root))
    ]


def _is_agent_zero_runtime_plugin_state(relative: Path) -> bool:
    if len(relative.parts) == 1 and relative.name in _AGENT_ZERO_RUNTIME_PLUGIN_FILE_NAMES:
        return True
    return bool(
        relative.parts
        and relative.parts[0] in _AGENT_ZERO_RUNTIME_PLUGIN_DIRECTORY_NAMES
    )


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def _yaml_bytes(value: Any) -> bytes:
    return yaml.safe_dump(
        value,
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    ).encode("utf-8")
