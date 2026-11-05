from __future__ import annotations

from pathlib import Path
import shutil
import time
from typing import Any, Callable

from acmm.experiment_package.provenance import experiment_deployment_provenance
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
    write_llm_baseline_state,
)
from acmm_infra.docker import docker_client, require_success
from acmm_infra.open_webui_api import open_webui_api_client, open_webui_api_error
from acmm_infra.profiles.open_webui import managed_open_webui_tool, open_webui_profile


class open_webui_manager:
    def __init__(
        self,
        repo_root: str | Path,
        runtime_root: str | Path,
        profile: open_webui_profile,
        docker: docker_client,
        api: open_webui_api_client | None = None,
        experiment_id: str | None = None,
        deployment_provenance: experiment_deployment_provenance | None = None,
        apparatus_source_sha256: str | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.profile = profile
        self.docker = docker
        self.experiment_id = experiment_id
        self.deployment_provenance = deployment_provenance
        self.apparatus_source_sha256 = apparatus_source_sha256
        self._validate_deployment_provenance("open-webui")
        self.deployment_root = (
            self.runtime_root / profile.deployment_directory
        ).resolve()
        self.data_root = (
            self.deployment_root / profile.data_directory
        ).resolve()
        self.defaults_root = (
            self.repo_root / "infrastructure" / "open-webui" / "defaults"
        ).resolve()
        self.open_webui_root = (
            self.repo_root / "infrastructure" / "open-webui"
        ).resolve()
        self.api = api or open_webui_api_client(
            f"http://127.0.0.1:{profile.web_port}"
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

    def _apparatus_source_sha256(
        self,
        infrastructure_subject: str,
        experiment_id: str,
    ) -> str | None:
        del infrastructure_subject
        provenance = self.deployment_provenance
        if provenance is not None:
            return provenance.apparatus_sha256
        if self.apparatus_source_sha256 is not None:
            return self.apparatus_source_sha256
        raise infrastructure_error(
            f"Experiment apparatus '{experiment_id}' has no package provenance or "
            "explicit apparatus source identity."
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

    def up(self, *, timeout_seconds: float = 60.0) -> None:
        if timeout_seconds <= 0:
            raise infrastructure_error(
                "Open WebUI startup timeout must be greater than zero seconds."
            )

        self.initialize_deployment()
        require_success(self.docker.compose_config(), "Docker Compose validation")
        require_success(self.docker.up(), "Starting Open WebUI infrastructure")
        self._wait_until_ready(timeout_seconds)

    def down(self) -> None:
        require_success(self.docker.down(), "Stopping Open WebUI infrastructure")

    def apply(
        self,
        *,
        timeout_seconds: float = 120.0,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if timeout_seconds <= 0:
            raise infrastructure_error(
                "Open WebUI apply timeout must be greater than zero seconds."
            )

        report = progress if progress is not None else lambda message: None
        experiment_id = self.experiment_id
        report("[1/4] Reconciling deployment files and environment.")
        self.initialize_deployment()

        if self.docker.container_running():
            raise infrastructure_error(
                "Open WebUI is running. Run 'acmm-infra open-webui down' before apply."
            )

        self._reconcile_deployment()
        require_success(self.docker.compose_config(), "Docker Compose validation")

        report("[2/4] Starting temporary Open WebUI provisioning service.")
        require_success(self.docker.up(), "Starting Open WebUI provisioning service")

        provisioned = False
        application_result: dict[str, Any] = {}
        tools_result: dict[str, Any] = {}

        try:
            self._wait_until_ready(timeout_seconds)
            session = self._admin_session()
            token = _required_text(session, "token", "Open WebUI session")
            desired_ids = {tool.id for tool in self.profile.tools}
            self._remove_stale_experiment_tools(token, desired_ids=desired_ids)

            report("[3/4] Reconciling persistent application configuration.")
            application_result = self._reconcile_application_config(token)

            if experiment_id is None:
                report("[4/4] Reconciling baseline tool policy.")
            else:
                report(
                    "[4/4] Reconciling Open WebUI apparatus for experiment "
                    f"'{experiment_id}'."
                )
            tools_result = self._reconcile_tools(token, session)
            provisioned = True
        finally:
            stop_result = self.docker.down()
            if provisioned:
                require_success(stop_result, "Stopping Open WebUI provisioning service")

        source_sha256: str | None = None
        if experiment_id is None:
            clear_experiment_apparatus_state(self.deployment_root)
        else:
            source_sha256 = self._apparatus_source_sha256(
                "open-webui",
                experiment_id,
            )
            write_experiment_apparatus_state(
                self.deployment_root,
                experiment_id=experiment_id,
                source_sha256=source_sha256,
                application_scope="experiment",
                resources=[
                    {"kind": "tool", "id": tool.id}
                    for tool in self.profile.tools
                ],
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
            "ollama_base_url": self.profile.ollama_base_url,
            "model_default": self.profile.model_default,
            "webui_auth": self.profile.webui_auth,
            "secret_key_file": self.profile.secret_key_file,
            "ollama_api_enabled": self.profile.ollama_api_enabled,
            "openai_api_enabled": self.profile.openai_api_enabled,
            "plugins_enabled": self.profile.plugins_enabled,
            "pip_install_frontmatter_requirements_enabled": (
                self.profile.pip_install_frontmatter_requirements_enabled
            ),
            "telemetry_enabled": self.profile.telemetry_enabled,
            "application": application_result,
            "tools": tools_result,
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
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]:
        if not self.profile.tools:
            raise infrastructure_error(
                "Open WebUI experiment profile contains no managed apparatus tools."
            )
        if timeout_seconds <= 0:
            raise infrastructure_error(
                "Open WebUI apparatus-apply timeout must be greater than zero seconds."
            )
        if not self.docker.container_running():
            raise infrastructure_error(
                "Open WebUI is stopped. Run 'acmm-infra open-webui up' before "
                "apparatus application."
            )

        self._wait_until_ready(timeout_seconds)
        session = self._admin_session()
        token = _required_text(session, "token", "Open WebUI session")
        desired_ids = {tool.id for tool in self.profile.tools}
        self._remove_stale_experiment_tools(token, desired_ids=desired_ids)
        result = self._reconcile_tools(token, session)
        source_sha256 = self._apparatus_source_sha256(
            "open-webui",
            experiment_id,
        )
        write_experiment_apparatus_state(
            self.deployment_root,
            experiment_id=experiment_id,
            source_sha256=source_sha256,
            application_scope=application_scope,
            resources=[
                {"kind": "tool", "id": tool.id}
                for tool in self.profile.tools
            ],
            package_provenance=self.deployment_provenance,
        )
        return {
            **result,
            "experiment_id": experiment_id,
            "source_sha256": source_sha256,
            "application_scope": application_scope,
        }

    def apply_tools(
        self,
        experiment_id: str | None,
        *,
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]:
        if experiment_id is not None:
            return self.apply_experiment(
                experiment_id,
                application_scope="tools",
                timeout_seconds=timeout_seconds,
            )

        if timeout_seconds <= 0:
            raise infrastructure_error(
                "Open WebUI apply-tools timeout must be greater than zero seconds."
            )
        if not self.docker.container_running():
            raise infrastructure_error(
                "Open WebUI is stopped. Run 'acmm-infra open-webui up' before "
                "applying baseline tool policy."
            )

        self._wait_until_ready(timeout_seconds)
        session = self._admin_session()
        token = _required_text(session, "token", "Open WebUI session")
        self._remove_stale_experiment_tools(token, desired_ids=set())
        result = self._reconcile_tools(token, session)
        clear_experiment_apparatus_state(self.deployment_root)
        return {
            **result,
            "experiment_id": None,
            "application_scope": "tools",
        }

    def status(self) -> dict[str, Any]:
        running, image = self.docker.container_info()
        compose_path = self.deployment_root / "compose.yaml"
        env_path = self.deployment_root / ".env"
        default_compose = self.defaults_root / "compose.yaml"
        observed_version = None

        if running:
            result = self.docker.exec(
                [
                    "python",
                    "-c",
                    "from open_webui.env import VERSION; print(VERSION)",
                ]
            )
            if result.succeeded:
                observed_version = result.stdout.strip() or None

        env_values = read_env(env_path) if env_path.is_file() else {}
        backend = self._backend_status() if running else _empty_backend_status(self.profile)

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
                "ollama_base_url": self.profile.ollama_base_url,
                "model_default": self.profile.model_default,
                "webui_auth": self.profile.webui_auth,
                "secret_key_file": self.profile.secret_key_file,
                "ollama_api_enabled": self.profile.ollama_api_enabled,
                "openai_api_enabled": self.profile.openai_api_enabled,
                "ollama_config": self._desired_ollama_config(),
                "plugins_enabled": self.profile.plugins_enabled,
                "pip_install_frontmatter_requirements_enabled": (
                    self.profile.pip_install_frontmatter_requirements_enabled
                ),
                "telemetry_enabled": self.profile.telemetry_enabled,
                "experiment_isolation": self._desired_isolation_config(),
                "managed_tools": tuple(tool.id for tool in self.profile.tools),
            },
            "observed": {
                "container_running": running,
                "container_image": image,
                "version": observed_version,
                "managed_env": {
                    key: env_values.get(key)
                    for key in self._managed_env()
                },
                "backend": backend,
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
            expected_apparatus_hash = self._apparatus_source_sha256(
                "open-webui",
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

        running = observed["container_running"]
        checks.append(
            doctor_check(
                "container_running",
                running,
                "running" if running else "Open WebUI container is stopped",
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
                    "open_webui_version",
                    version_ok,
                    f"observed {version!r}; expected {desired['version']!r}",
                )
            )

            backend = observed["backend"]
            api_ok = backend["authenticated"]
            api_detail = (
                f"authenticated as {backend['email']} ({backend['role']})"
                if api_ok
                else backend["error"] or "backend authentication failed"
            )
            checks.append(doctor_check("backend_api", api_ok, api_detail))

            if api_ok:
                ollama_config = backend["ollama_config"]
                ollama_ok = _ollama_config_matches(
                    ollama_config,
                    desired["ollama_config"],
                )
                checks.append(
                    doctor_check(
                        "ollama_config",
                        ollama_ok,
                        _ollama_config_detail(
                            ollama_config,
                            desired["ollama_config"],
                        ),
                    )
                )

                openai_config = backend["openai_config"]
                openai_ok = (
                    openai_config.get("ENABLE_OPENAI_API")
                    == desired["openai_api_enabled"]
                )
                checks.append(
                    doctor_check(
                        "openai_config",
                        openai_ok,
                        (
                            "OpenAI provider enablement matches profile"
                            if openai_ok
                            else "OpenAI provider enablement drifted from profile"
                        ),
                    )
                )

                isolation_config = backend["isolation_config"]
                isolation_ok = _config_subset_matches(
                    isolation_config,
                    desired["experiment_isolation"],
                )
                checks.append(
                    doctor_check(
                        "experiment_isolation",
                        isolation_ok,
                        (
                            "persistent experiment-isolation settings match profile"
                            if isolation_ok
                            else "persistent experiment-isolation settings drifted"
                        ),
                    )
                )

                model_ok = backend["model_available"]
                checks.append(
                    doctor_check(
                        "provider_model_available",
                        model_ok,
                        (
                            f"model {desired['model_default']!r} is available"
                            if model_ok
                            else _missing_model_detail(
                                desired["model_default"],
                                backend["model_ids"],
                            )
                        ),
                    )
                )

                for tool_id in desired["managed_tools"]:
                    tool_state = backend["managed_tools"][tool_id]
                    checks.append(
                        doctor_check(
                            f"managed_tool:{tool_id}",
                            tool_state["exact"],
                            _tool_state_detail(tool_id, tool_state),
                        )
                    )

        return doctor_report(tuple(checks))

    def reset(self, scope: str) -> tuple[Path, ...]:
        if self.docker.container_running():
            raise infrastructure_error(
                "Open WebUI is running. Run 'acmm-infra open-webui down' before reset."
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
                raise infrastructure_error("Refusing reset outside Open WebUI data root.")
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
        values.update(self._managed_env())
        write_env(
            env_path,
            values,
            header_lines=(
                "# ACMM Open WebUI deployment configuration.",
                "# Managed keys are reconciled by 'acmm-infra open-webui apply'.",
            ),
            line_ending="\r\n",
        )

    def _wait_until_ready(self, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_error = "backend did not report ready"

        while True:
            if not self.docker.container_running():
                raise infrastructure_error(
                    "Open WebUI container stopped before the backend became ready."
                )

            try:
                readiness = self.api.get_readiness()
                if readiness.get("status") is True:
                    return
                last_error = f"unexpected readiness response: {readiness!r}"
            except open_webui_api_error as exc:
                last_error = str(exc)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise infrastructure_error(
                    "Timed out waiting for Open WebUI backend readiness: "
                    f"{last_error}"
                )

            time.sleep(min(0.25, remaining))

    def _admin_session(self) -> dict[str, Any]:
        session = self.api.authenticate_no_auth_admin()
        role = _required_text(session, "role", "Open WebUI session")

        if role != "admin":
            raise infrastructure_error(
                f"Open WebUI provisioning requires an admin session; observed role {role!r}."
            )

        return session

    def _reconcile_application_config(self, token: str) -> dict[str, Any]:
        desired_ollama = self._desired_ollama_config()
        observed_ollama = self.api.get_ollama_config(token)
        ollama_changed = not _ollama_config_matches(observed_ollama, desired_ollama)

        if ollama_changed:
            observed_ollama = self.api.update_ollama_config(token, desired_ollama)

        observed_openai = self.api.get_openai_config(token)
        openai_changed = (
            observed_openai.get("ENABLE_OPENAI_API")
            != self.profile.openai_api_enabled
        )

        if openai_changed:
            openai_payload = {
                "ENABLE_OPENAI_API": self.profile.openai_api_enabled,
                "OPENAI_API_BASE_URLS": _list_value(
                    observed_openai.get("OPENAI_API_BASE_URLS")
                ),
                "OPENAI_API_KEYS": _list_value(
                    observed_openai.get("OPENAI_API_KEYS")
                ),
                "OPENAI_API_CONFIGS": _dict_value(
                    observed_openai.get("OPENAI_API_CONFIGS")
                ),
            }
            observed_openai = self.api.update_openai_config(
                token,
                openai_payload,
            )

        desired_isolation = self._desired_isolation_config()
        observed_config = self.api.get_config(token)
        isolation_changed = not _config_subset_matches(
            observed_config,
            desired_isolation,
        )
        if isolation_changed:
            observed_config = self.api.import_config(token, desired_isolation)

        return {
            "ollama_changed": ollama_changed,
            "ollama_config": observed_ollama,
            "openai_changed": openai_changed,
            "openai_config": observed_openai,
            "isolation_changed": isolation_changed,
            "isolation_config": _config_subset(observed_config, desired_isolation),
        }

    def _remove_stale_experiment_tools(
        self,
        token: str,
        *,
        desired_ids: set[str],
    ) -> None:
        candidates: set[str] = set()
        state = read_experiment_apparatus_state(self.deployment_root)
        if state is not None:
            resources = state.get("resources")
            if isinstance(resources, list):
                for resource in resources:
                    if (
                        isinstance(resource, dict)
                        and resource.get("kind") == "tool"
                        and isinstance(resource.get("id"), str)
                    ):
                        candidates.add(resource["id"])

        for tool_id in sorted(candidates - desired_ids):
            if self.api.get_tool(token, tool_id) is not None:
                self.api.delete_tool(token, tool_id)

    def _reconcile_tools(
        self,
        token: str,
        session: dict[str, Any],
    ) -> dict[str, Any]:
        created: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []

        for tool in self.profile.tools:
            payload = self._tool_payload(tool)
            observed = self.api.get_tool(token, tool.id)

            if observed is None:
                self.api.create_tool(token, payload)
                created.append(tool.id)
                continue

            if _tool_matches_payload(observed, payload):
                unchanged.append(tool.id)
                continue

            update_payload = dict(payload)
            grants = observed.get("access_grants")
            if isinstance(grants, list):
                update_payload["access_grants"] = grants
            self.api.update_tool(token, tool.id, update_payload)
            updated.append(tool.id)

        return {
            "admin_email": session.get("email"),
            "admin_role": session.get("role"),
            "created": tuple(created),
            "updated": tuple(updated),
            "unchanged": tuple(unchanged),
            "managed_tools": tuple(tool.id for tool in self.profile.tools),
        }

    def _desired_ollama_config(self) -> dict[str, Any]:
        return {
            "ENABLE_OLLAMA_API": self.profile.ollama_api_enabled,
            "OLLAMA_BASE_URLS": [self.profile.ollama_base_url],
            "OLLAMA_API_CONFIGS": {},
        }

    def _desired_isolation_config(self) -> dict[str, Any]:
        isolation = self.profile.experiment_isolation
        desired = {
            "direct.enable": isolation.direct_connections_enabled,
            "direct.integrations.enable": isolation.direct_integrations_enabled,
            "web.search.enable": isolation.web_search_enabled,
            "code_execution.enable": isolation.code_execution_enabled,
            "code_interpreter.enable": isolation.code_interpreter_enabled,
            "memories.enable": isolation.memories_enabled,
            "memories.system_context.enable": isolation.memory_system_context_enabled,
            "memories.background_review.enable": (
                isolation.memory_background_review_enabled
            ),
            "image_generation.enable": isolation.image_generation_enabled,
            "image_generation.prompt.enable": isolation.image_prompt_generation_enabled,
            "task.title.enable": isolation.title_generation_enabled,
            "task.tags.enable": isolation.tags_generation_enabled,
            "task.follow_up.enable": isolation.follow_up_generation_enabled,
            "task.autocomplete.enable": isolation.autocomplete_generation_enabled,
            "task.query.search.enable": isolation.search_query_generation_enabled,
            "task.query.retrieval.enable": isolation.retrieval_query_generation_enabled,
        }
        if not isolation.external_tool_servers_enabled:
            desired["tool_server.connections"] = []
        if not isolation.terminal_servers_enabled:
            desired["terminal_server.connections"] = []
        _apply_config_overrides(
            desired,
            self.profile.managed_config_overrides,
            "Open WebUI managed persistent-config overrides",
        )
        return desired

    def _backend_status(self) -> dict[str, Any]:
        state = _empty_backend_status(self.profile)

        try:
            session = self.api.authenticate_no_auth_admin()
            token = _required_text(session, "token", "Open WebUI session")
            state["authenticated"] = True
            state["email"] = session.get("email")
            state["role"] = session.get("role")
            state["ollama_config"] = self.api.get_ollama_config(token)
            state["openai_config"] = self.api.get_openai_config(token)
            state["isolation_config"] = _config_subset(
                self.api.get_config(token),
                self._desired_isolation_config(),
            )

            models = self.api.get_models(token)
            model_ids = tuple(
                model_id
                for model in models
                if isinstance((model_id := model.get("id")), str)
            )
            state["model_ids"] = model_ids
            state["model_available"] = self.profile.model_default in model_ids
            matching_model = next(
                (
                    model
                    for model in models
                    if model.get("id") == self.profile.model_default
                ),
                None,
            )
            context_window, context_source = _model_context_observation(
                matching_model
            )
            state["model_context_window"] = context_window
            state["model_context_source"] = context_source

            tool_states: dict[str, dict[str, Any]] = {}
            for tool in self.profile.tools:
                expected = self._tool_payload(tool)
                observed = self.api.get_tool(token, tool.id)
                tool_states[tool.id] = _tool_status(observed, expected)
            state["managed_tools"] = tool_states
        except (open_webui_api_error, infrastructure_error) as exc:
            state["error"] = str(exc)

        return state

    def _tool_payload(self, tool: managed_open_webui_tool) -> dict[str, Any]:
        source_root = (tool.source_root or self.open_webui_root).resolve()
        source = (source_root / tool.source).resolve()
        if not source.is_relative_to(source_root):
            raise infrastructure_error(
                f"Managed Open WebUI tool source escapes its source root: '{source}'."
            )
        if not source.is_file():
            raise infrastructure_error(
                f"Managed Open WebUI tool source does not exist: '{source}'."
            )

        return {
            "id": tool.id,
            "name": tool.name,
            "content": source.read_text(encoding="utf-8"),
            "meta": {
                "description": tool.description,
            },
            "access_grants": [],
        }

    def _managed_env(self) -> dict[str, str]:
        telemetry = self.profile.telemetry_enabled
        values = {
            "OPEN_WEBUI_IMAGE": self.profile.image,
            "OPEN_WEBUI_CONTAINER_NAME": self.profile.container_name,
            "OPEN_WEBUI_PORT": str(self.profile.web_port),
            "OPEN_WEBUI_OLLAMA_BASE_URL": self.profile.ollama_base_url,
            "OPEN_WEBUI_AUTH": bool_text(self.profile.webui_auth),
            "OPEN_WEBUI_SECRET_KEY_FILE": self.profile.secret_key_file,
            "OPEN_WEBUI_ENABLE_OLLAMA_API": bool_text(
                self.profile.ollama_api_enabled
            ),
            "OPEN_WEBUI_ENABLE_OPENAI_API": bool_text(
                self.profile.openai_api_enabled
            ),
            "OPEN_WEBUI_ENABLE_PLUGINS": bool_text(
                self.profile.plugins_enabled
            ),
            "OPEN_WEBUI_ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS": bool_text(
                self.profile.pip_install_frontmatter_requirements_enabled
            ),
            **_isolation_env(self.profile),
            "OPEN_WEBUI_SCARF_NO_ANALYTICS": bool_text(not telemetry),
            "OPEN_WEBUI_DO_NOT_TRACK": bool_text(not telemetry),
            "OPEN_WEBUI_ANONYMIZED_TELEMETRY": bool_text(telemetry),
        }
        _apply_string_overrides(
            values,
            self.profile.managed_environment_overrides,
            "Open WebUI managed environment overrides",
        )
        return values


def _apply_string_overrides(
    values: dict[str, str],
    overrides: tuple[tuple[str, str], ...],
    description: str,
) -> None:
    keys = [key for key, _ in overrides]
    if len(keys) != len(set(keys)):
        raise infrastructure_error(f"{description} must have unique keys.")
    for key, value in overrides:
        if not key:
            raise infrastructure_error(f"{description} keys must be non-empty.")
        values[key] = value


def _apply_config_overrides(
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
        if key in values:
            raise infrastructure_error(
                f"{description} must not override baseline-managed key {key!r}."
            )
        values[key] = value


def _isolation_env(profile: open_webui_profile) -> dict[str, str]:
    isolation = profile.experiment_isolation
    return {
        "OPEN_WEBUI_ENABLE_DIRECT_CONNECTIONS": bool_text(
            isolation.direct_connections_enabled
        ),
        "OPEN_WEBUI_ENABLE_DIRECT_INTEGRATIONS": bool_text(
            isolation.direct_integrations_enabled
        ),
        "OPEN_WEBUI_ENABLE_WEB_SEARCH": bool_text(isolation.web_search_enabled),
        "OPEN_WEBUI_ENABLE_CODE_EXECUTION": bool_text(
            isolation.code_execution_enabled
        ),
        "OPEN_WEBUI_ENABLE_CODE_INTERPRETER": bool_text(
            isolation.code_interpreter_enabled
        ),
        "OPEN_WEBUI_ENABLE_MEMORIES": bool_text(isolation.memories_enabled),
        "OPEN_WEBUI_ENABLE_MEMORY_SYSTEM_CONTEXT": bool_text(
            isolation.memory_system_context_enabled
        ),
        "OPEN_WEBUI_ENABLE_MEMORY_BACKGROUND_REVIEW": bool_text(
            isolation.memory_background_review_enabled
        ),
        "OPEN_WEBUI_ENABLE_IMAGE_GENERATION": bool_text(
            isolation.image_generation_enabled
        ),
        "OPEN_WEBUI_ENABLE_IMAGE_PROMPT_GENERATION": bool_text(
            isolation.image_prompt_generation_enabled
        ),
        "OPEN_WEBUI_ENABLE_TITLE_GENERATION": bool_text(
            isolation.title_generation_enabled
        ),
        "OPEN_WEBUI_ENABLE_TAGS_GENERATION": bool_text(
            isolation.tags_generation_enabled
        ),
        "OPEN_WEBUI_ENABLE_FOLLOW_UP_GENERATION": bool_text(
            isolation.follow_up_generation_enabled
        ),
        "OPEN_WEBUI_ENABLE_AUTOCOMPLETE_GENERATION": bool_text(
            isolation.autocomplete_generation_enabled
        ),
        "OPEN_WEBUI_ENABLE_SEARCH_QUERY_GENERATION": bool_text(
            isolation.search_query_generation_enabled
        ),
        "OPEN_WEBUI_ENABLE_RETRIEVAL_QUERY_GENERATION": bool_text(
            isolation.retrieval_query_generation_enabled
        ),
        "OPEN_WEBUI_TOOL_SERVER_CONNECTIONS": "[]",
        "OPEN_WEBUI_TERMINAL_SERVER_CONNECTIONS": "[]",
    }


def _config_subset(
    observed: dict[str, Any],
    desired: dict[str, Any],
) -> dict[str, Any]:
    return {key: observed.get(key) for key in desired}


def _config_subset_matches(
    observed: dict[str, Any],
    desired: dict[str, Any],
) -> bool:
    return all(observed.get(key) == value for key, value in desired.items())


def _model_context_observation(
    model: dict[str, Any] | None,
) -> tuple[int | None, str | None]:
    if not isinstance(model, dict):
        return None, None
    field_names = (
        "context_window",
        "context_length",
        "num_ctx",
        "num_context",
        "context_size",
    )
    containers: tuple[tuple[str, dict[str, Any]], ...] = (("model", model),)
    nested: list[tuple[str, dict[str, Any]]] = []
    for key in ("info", "details", "meta", "ollama"):
        value = model.get(key)
        if isinstance(value, dict):
            nested.append((key, value))
    containers = containers + tuple(nested)
    for container_name, container in containers:
        for field_name in field_names:
            value = container.get(field_name)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value, f"open_webui_model_catalog:{container_name}.{field_name}"
            if isinstance(value, str) and value.isdigit() and int(value) > 0:
                return int(value), (
                    f"open_webui_model_catalog:{container_name}.{field_name}"
                )
    return None, None


def _empty_backend_status(profile: open_webui_profile) -> dict[str, Any]:
    return {
        "authenticated": False,
        "email": None,
        "role": None,
        "ollama_config": {},
        "openai_config": {},
        "isolation_config": {},
        "model_available": False,
        "model_ids": (),
        "model_context_window": None,
        "model_context_source": None,
        "managed_tools": {
            tool.id: {
                "present": False,
                "exact": False,
            }
            for tool in profile.tools
        },
        "error": None,
    }


def _tool_status(
    observed: dict[str, Any] | None,
    expected: dict[str, Any],
) -> dict[str, Any]:
    if observed is None:
        return {
            "present": False,
            "exact": False,
        }

    return {
        "present": True,
        "exact": _tool_matches_payload(observed, expected),
        "name": observed.get("name"),
        "description": _tool_description(observed),
    }


def _tool_matches_payload(
    observed: dict[str, Any],
    expected: dict[str, Any],
) -> bool:
    expected_meta = expected.get("meta")
    expected_description = None
    if isinstance(expected_meta, dict):
        expected_description = expected_meta.get("description")

    return (
        observed.get("id") == expected.get("id")
        and observed.get("name") == expected.get("name")
        and observed.get("content") == expected.get("content")
        and _tool_description(observed) == expected_description
    )


def _tool_description(tool: dict[str, Any]) -> Any:
    meta = tool.get("meta")
    if not isinstance(meta, dict):
        return None
    return meta.get("description")


def _ollama_config_matches(
    observed: dict[str, Any],
    expected: dict[str, Any],
) -> bool:
    return (
        observed.get("ENABLE_OLLAMA_API") == expected.get("ENABLE_OLLAMA_API")
        and _list_value(observed.get("OLLAMA_BASE_URLS"))
        == _list_value(expected.get("OLLAMA_BASE_URLS"))
        and _dict_value(observed.get("OLLAMA_API_CONFIGS"))
        == _dict_value(expected.get("OLLAMA_API_CONFIGS"))
    )


def _ollama_config_detail(
    observed: dict[str, Any],
    expected: dict[str, Any],
) -> str:
    if _ollama_config_matches(observed, expected):
        return "persistent Ollama provider configuration matches profile"

    return (
        "persistent Ollama provider configuration drifted: "
        f"observed enabled={observed.get('ENABLE_OLLAMA_API')!r}, "
        f"base_urls={_list_value(observed.get('OLLAMA_BASE_URLS'))!r}; "
        f"expected enabled={expected.get('ENABLE_OLLAMA_API')!r}, "
        f"base_urls={_list_value(expected.get('OLLAMA_BASE_URLS'))!r}"
    )


def _missing_model_detail(model: str, model_ids: tuple[str, ...]) -> str:
    if not model_ids:
        return f"model {model!r} is not available; Open WebUI reported no models"

    preview = ", ".join(model_ids[:8])
    suffix = "" if len(model_ids) <= 8 else ", ..."
    return (
        f"model {model!r} is not available; observed models: "
        f"{preview}{suffix}"
    )


def _list_value(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _tool_state_detail(tool_id: str, state: dict[str, Any]) -> str:
    if not state["present"]:
        return f"managed tool {tool_id!r} is missing"
    if not state["exact"]:
        return f"managed tool {tool_id!r} drifted from repository source"
    return f"managed tool {tool_id!r} matches repository source"


def _required_text(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise infrastructure_error(f"{context} did not provide non-empty {key!r}.")
    return value


