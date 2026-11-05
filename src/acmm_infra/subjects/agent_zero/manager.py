from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


from acmm_infra.subjects.agent_zero.api import agent_zero_api_client, agent_zero_api_error
from acmm.experiment_package.provenance import experiment_deployment_provenance
from acmm_infra.common import doctor_check, doctor_report, infrastructure_error
from acmm_infra.deployment import (
    bool_text,
    clear_experiment_package_state,
    experiment_package_state_matches,
    files_equal,
    install_default_if_missing,
    read_env,
    read_experiment_package_state,
    write_env,
    write_experiment_package_state,
    write_llm_baseline_state,
)
from acmm_infra.docker import docker_client, require_success
from acmm_infra.subjects.agent_zero.profile import agent_zero_profile


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
        deployment_provenance: experiment_deployment_provenance | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.profile = profile
        self.docker = docker
        self.experiment_id = experiment_id
        self.deployment_provenance = deployment_provenance
        self._validate_deployment_provenance("agent-zero")
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

    def _validate_deployment_provenance(self, infrastructure_subject: str) -> None:
        provenance = self.deployment_provenance
        if provenance is None:
            return
        if self.experiment_id is None:
            raise infrastructure_error(
                "Experiment Package deployment provenance requires an experiment ID."
            )
        if provenance.package.experiment_id != self.experiment_id:
            raise infrastructure_error(
                "Experiment Package provenance experiment ID does not match manager "
                "experiment ID."
            )
        if provenance.infrastructure_subject != infrastructure_subject:
            raise infrastructure_error(
                "Experiment Package deployment provenance targets infrastructure "
                f"'{provenance.infrastructure_subject}', expected "
                f"'{infrastructure_subject}'."
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
        file_state: dict[str, str] = {}
        model_state = "unknown"
        settings_state = "unknown"
        live_state: dict[str, Any] | None = None
        try:
            self._wait_until_ready(timeout_seconds)
            report("[3/5] Reconciling managed profile and isolation files.")
            file_state = self._reconcile_application_files_live()

            report("[4/5] Reconciling model preset and persistent settings.")
            model_state = self._reconcile_model_preset()
            settings_state = self._reconcile_persistent_settings()
            live_state = self._observe_live_application()
        finally:
            require_success(self.docker.down(), "Stopping Agent Zero provisioning service")

        report("[5/5] Agent Zero configuration reconciled and verified.")
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

        if experiment_id is None:
            clear_experiment_package_state(self.deployment_root)
        else:
            if self.deployment_provenance is None:
                raise infrastructure_error(
                    "Experiment Package Agent Zero apply requires deployment provenance."
                )
            write_experiment_package_state(
                self.deployment_root,
                experiment_id=experiment_id,
                resources=self._experiment_resources(),
                package_provenance=self.deployment_provenance,
            )

        write_llm_baseline_state(
            self.deployment_root,
            llm_baseline_id=self.profile.llm_baseline_id,
            model_name=self.profile.model_default,
            logical_provider=self.profile.llm_logical_provider,
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
            "application_files": file_state,
            "model_preset_state": model_state,
            "persistent_settings": settings_state,
            "provisioning_service": "stopped after apply",
            "experiment_id": experiment_id,
            "package_sha256": (
                self.deployment_provenance.package.package_sha256
                if self.deployment_provenance is not None
                else None
            ),
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
        application_files = self._application_file_status()
        live_application = self._observe_live_application() if running else None

        return {
            "runtime_root": str(self.runtime_root),
            "deployment_root": str(self.deployment_root),
            "experiment_package": read_experiment_package_state(
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
            },
            "observed": {
                "container_running": running,
                "container_image": image,
                "version": observed_version,
                "managed_env": {
                    key: env_values.get(key)
                    for key in self._managed_env()
                },
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

        marker = status["experiment_package"]
        package_ok, package_detail = experiment_package_state_matches(
            marker,
            experiment_id=self.experiment_id,
            provenance=self.deployment_provenance,
        )
        checks.append(
            doctor_check(
                "experiment_package_identity",
                package_ok,
                package_detail,
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
                        "provider_model_available",
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
                checks.append(doctor_check("provider_model_available", False, detail))

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

    def _experiment_resources(self) -> list[dict[str, str]]:
        resources = [
            {"kind": "managed_environment", "key": key}
            for key, _ in self.profile.managed_environment_overrides
        ]
        resources.extend(
            {"kind": "managed_setting", "key": key}
            for key, _ in self.profile.managed_settings_overrides
        )
        return resources

    def _managed_env(self) -> dict[str, str]:
        isolation = self.profile.experiment_isolation
        values = {
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
        _apply_overrides(
            values,
            self.profile.managed_environment_overrides,
            "Agent Zero managed environment overrides",
        )
        return values

    def _managed_settings(self) -> dict[str, Any]:
        isolation = self.profile.experiment_isolation
        values: dict[str, Any] = {
            "agent_profile": self.profile.agent_profile,
            "mcp_servers": '{"mcpServers":{}}',
            "mcp_server_enabled": isolation.mcp_server_enabled,
            "a2a_server_enabled": isolation.a2a_server_enabled,
            "update_check_enabled": isolation.update_check_enabled,
            "chat_inherit_project": isolation.chat_inherit_project,
            "rfc_auto_docker": isolation.rfc_auto_docker,
        }
        _apply_overrides(
            values,
            self.profile.managed_settings_overrides,
            "Agent Zero managed settings overrides",
        )
        return values

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
        payload = _load_json_payload("model_preset.json")
        if not isinstance(payload, list) or len(payload) != 1:
            raise infrastructure_error("Agent Zero model preset payload is invalid.")
        preset = payload[0]
        if not isinstance(preset, dict):
            raise infrastructure_error("Agent Zero model preset payload is invalid.")

        preset["name"] = self.profile.model_preset
        chat = preset["chat"]
        utility = preset["utility"]
        embedding = preset["embedding"]
        chat.update(
            provider=self.profile.model_provider,
            name=self.profile.model_default,
            api_base=self.profile.model_base_url,
            ctx_length=self.profile.context_window,
        )
        utility.update(
            provider=self.profile.utility_model_provider,
            name=self.profile.utility_model_default,
            api_base=self.profile.utility_model_base_url,
            ctx_length=self.profile.utility_context_window,
        )
        embedding.update(
            provider=self.profile.embedding_model_provider,
            name=self.profile.embedding_model_default,
        )
        return payload

    def _application_file_payloads(self) -> dict[Path, bytes]:
        isolation = self.profile.experiment_isolation
        profile_root = Path("agents") / self.profile.agent_profile
        payload_root = Path(__file__).with_name("payloads")

        tool_policy = _load_json_payload("tool_access.json")
        tool_policy["default"] = isolation.tool_default
        tool_policy["mcp_default"] = isolation.mcp_tool_default
        tool_policy["allowed"] = list(isolation.allowed_tools)

        skills = _load_json_payload("skills.json")
        skills["visibility_policy"]["default"] = isolation.skills_default

        memory = _load_json_payload("memory.json")
        memory["memory_recall_enabled"] = isolation.memory_recall_enabled
        memory["memory_memorize_enabled"] = isolation.memory_memorize_enabled
        memory["memory_memorize_consolidation"] = (
            isolation.memory_memorize_consolidation
        )

        chat_naming = _load_json_payload("chat_naming.json")
        chat_naming["automatic_naming"] = isolation.automatic_chat_naming

        prompt_root = self.agent_zero_root / "profile-prompts"
        return {
            profile_root / "agent.yaml": (payload_root / "agent.yaml").read_bytes(),
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
            model_config = model_response.get("config")
            if not isinstance(model_config, dict):
                model_config = {}
            chat_model = model_config.get("chat_model")
            utility_model = model_config.get("utility_model")
            if not isinstance(chat_model, dict):
                chat_model = {}
            if not isinstance(utility_model, dict):
                utility_model = {}

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
                "observed_model": chat_model.get("name"),
                "observed_context_window": chat_model.get("ctx_length"),
                "observed_utility_model": utility_model.get("name"),
                "observed_utility_context_window": utility_model.get("ctx_length"),
                "model_available": model_available,
                "models_observed": model_names,
                "model_availability_detail": availability_detail,
                "error": None,
            }
        except (agent_zero_api_error, infrastructure_error) as exc:
            return {
                "settings_exact": False,
                "model_config_exact": False,
                "observed_model": None,
                "observed_context_window": None,
                "observed_utility_model": None,
                "observed_utility_context_window": None,
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

def _apply_overrides(
    values: dict[str, Any],
    overrides: tuple[tuple[str, Any], ...],
    description: str,
) -> None:
    keys = [key for key, _ in overrides]
    if len(keys) != len(set(keys)):
        raise infrastructure_error(f"{description} must have unique keys.")
    for key, value in overrides:
        if not key:
            raise infrastructure_error(f"{description} keys must be non-empty.")
        values[key] = value


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("utf-8")




def _load_json_payload(name: str) -> Any:
    path = Path(__file__).with_name("payloads") / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise infrastructure_error(
            f"Could not load Agent Zero payload '{path}': {exc}"
        ) from exc
