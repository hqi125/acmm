from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Literal

import yaml

from acmm.experiment_package.provenance import experiment_deployment_provenance
from acmm_infra.common import doctor_check, doctor_report, infrastructure_error
from acmm_infra.deployment import (
    clear_experiment_apparatus_state,
    experiment_application_scope,
    install_default_if_missing,
    read_experiment_apparatus_state,
    write_experiment_apparatus_state,
    write_llm_baseline_state,
)
from acmm_infra.docker import docker_client, require_success
from acmm_infra.profiles.experiments import (
    experiment_apparatus_sha256,
    registered_experiment_apparatus_ids,
    with_hermes_experiment,
)
from acmm_infra.profiles.hermes import hermes_profile, managed_hermes_plugin
from acmm_infra.source_tree import (
    copytree_ignore_generated,
    iter_managed_source_files,
)
from acmm_infra.hermes_client import hermes_client, managed_overlay_toolsets


HERMES_CONFIG_SUPPORT_FLOOR = 12


class hermes_manager:
    def __init__(
        self,
        repo_root: str | Path,
        runtime_root: str | Path,
        profile: hermes_profile,
        docker: docker_client,
        experiment_id: str | None = None,
        deployment_provenance: experiment_deployment_provenance | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.profile = profile
        self.docker = docker
        self.experiment_id = experiment_id
        self.deployment_provenance = deployment_provenance
        self._validate_deployment_provenance("hermes")
        self.client = hermes_client(docker)
        self.deployment_root = (
            self.runtime_root / profile.deployment_directory
        ).resolve()
        self.data_root = (
            self.deployment_root / profile.data_directory
        ).resolve()
        self.defaults_root = (
            self.repo_root
            / "infrastructure"
            / "hermes"
            / "defaults"
        ).resolve()

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
        provenance = self.deployment_provenance
        if provenance is not None:
            return provenance.apparatus_sha256
        return experiment_apparatus_sha256(
            self.repo_root,
            infrastructure_subject,
            experiment_id,
        )

    def init_runtime(self) -> None:
        self.deployment_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.data_root / "plugins").mkdir(parents=True, exist_ok=True)
        self._ensure_hermes_env()

    def initialize_deployment(self) -> dict[str, str]:
        self.init_runtime()

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
                "Hermes startup timeout must be greater than zero seconds."
            )

        self.initialize_deployment()
        require_success(self.docker.compose_config(), "Docker Compose validation")
        require_success(self.docker.up(), "Starting Hermes infrastructure")

    def down(self) -> None:
        require_success(self.docker.down(), "Stopping Hermes infrastructure")

    def apply(
        self,
        *,
        timeout_seconds: float = 120.0,
        progress: Callable[[str], None] | None = None,
        output_mode: Literal["quiet", "normal", "verbose"] = "normal",
    ) -> dict[str, Any]:
        if timeout_seconds <= 0:
            raise infrastructure_error(
                "Hermes apply timeout must be greater than zero seconds."
            )

        if output_mode not in {"quiet", "normal", "verbose"}:
            raise infrastructure_error(
                f"Unknown Hermes apply output mode: '{output_mode}'."
            )

        report = progress if progress is not None else lambda message: None
        experiment_id = self.experiment_id

        if output_mode != "quiet":
            report("[1/4] Verifying initialized Hermes deployment.")
        self.initialize_deployment()

        if self.docker.container_running():
            raise infrastructure_error(
                "Hermes gateway is running. Run 'acmm-infra hermes down' before "
                "applying infrastructure configuration."
            )

        self._validate_existing_config_version()

        desired_plugins = {
            (plugin.name, plugin.destination.as_posix())
            for plugin in self.profile.plugins
        }
        previous_state = read_experiment_apparatus_state(self.deployment_root)
        stale_plugins = self._stale_plugins(
            previous_state,
            desired_plugins=desired_plugins,
        )

        synchronized: list[str] = []
        if experiment_id is None:
            if output_mode != "quiet":
                report("[2/4] Reconciling experiment-independent Hermes state.")
        else:
            if output_mode != "quiet":
                report(
                    "[2/4] Synchronizing Hermes apparatus for experiment "
                    f"'{experiment_id}'."
                )
            for plugin in self.profile.plugins:
                self._sync_plugin(plugin)
                synchronized.append(plugin.name)

        provisioning_profile = replace(
            self.profile,
            plugins=tuple(self.profile.plugins) + tuple(stale_plugins),
        )
        operation_count = self.client.profile_operation_count(provisioning_profile)

        if output_mode != "quiet":
            target = (
                "experiment-specific Hermes state"
                if experiment_id is not None
                else "Hermes baseline state"
            )
            report(
                "[3/4] Applying "
                f"{operation_count} operations for {target} in one provisioning "
                f"container (timeout {timeout_seconds:g}s)."
            )

        self.client.apply_profile(
            provisioning_profile,
            timeout_seconds=timeout_seconds,
            output_mode=output_mode,
            output=report,
        )

        for plugin in stale_plugins:
            self._remove_plugin_destination(plugin.destination)

        source_sha256: str | None = None
        if experiment_id is None:
            clear_experiment_apparatus_state(self.deployment_root)
        else:
            source_sha256 = self._apparatus_source_sha256(
                "hermes",
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
                        "toolsets": ",".join(plugin.toolsets),
                    }
                    for plugin in self.profile.plugins
                ],
                package_provenance=self.deployment_provenance,
            )

        write_llm_baseline_state(
            self.deployment_root,
            llm_baseline_id=self.profile.llm_baseline_id,
            model_name=self.profile.model_default,
            logical_provider=self.profile.llm_logical_provider,
        )

        if output_mode != "quiet":
            if experiment_id is None:
                report("[4/4] Hermes baseline applied successfully.")
            else:
                report(
                    "[4/4] Hermes experiment environment applied successfully: "
                    f"{experiment_id}."
                )

        return {
            "model_provider": self.profile.model_provider,
            "model_base_url": self.profile.model_base_url,
            "model_api_mode": self.profile.model_api_mode,
            "model_default": self.profile.model_default,
            "context_window": self.profile.context_window,
            "reasoning_effort": self.profile.reasoning_effort,
            "terminal_backend": self.profile.terminal_backend,
            "blank_slate": self.profile.blank_slate.enabled,
            "blank_slate_toolsets": self.profile.blank_slate.minimal_toolsets,
            "experiment_isolation": {
                "title_generation_enabled": (
                    self.profile.experiment_isolation.title_generation_enabled
                ),
                "background_review_enabled": (
                    self.profile.experiment_isolation.background_review_enabled
                ),
                "tool_search_enabled": (
                    self.profile.experiment_isolation.tool_search_enabled
                ),
            },
            "experiment_id": experiment_id,
            "source_sha256": source_sha256,
            "application_scope": (
                "experiment" if experiment_id is not None else None
            ),
            "plugins_synchronized": synchronized,
            "provisioning_operations": operation_count,
        }


    def apply_experiment(
        self,
        experiment_id: str,
        *,
        application_scope: experiment_application_scope = "experiment",
        timeout_seconds: float = 120.0,
        progress: Callable[[str], None] | None = None,
        output_mode: Literal["quiet", "normal", "verbose"] = "normal",
    ) -> dict[str, Any]:
        if not self.profile.plugins:
            raise infrastructure_error(
                "Hermes experiment profile contains no managed apparatus plugins."
            )

        report = progress if progress is not None else lambda message: None
        self.initialize_deployment()
        if self.docker.container_running():
            raise infrastructure_error(
                "Hermes gateway is running. Run 'acmm-infra hermes down' before "
                "applying experiment apparatus."
            )
        self._validate_existing_config_version()

        desired_plugins = {
            (plugin.name, plugin.destination.as_posix())
            for plugin in self.profile.plugins
        }
        previous_state = read_experiment_apparatus_state(self.deployment_root)
        stale_plugins = self._stale_plugins(
            previous_state,
            desired_plugins=desired_plugins,
        )

        if output_mode != "quiet":
            report("[1/3] Synchronizing selected Hermes experiment apparatus.")
        synchronized: list[str] = []
        for plugin in self.profile.plugins:
            self._sync_plugin(plugin)
            synchronized.append(plugin.name)

        provisioning_profile = replace(
            self.profile,
            plugins=tuple(self.profile.plugins) + tuple(stale_plugins),
        )
        operation_count = self.client.tool_operation_count(provisioning_profile)
        if output_mode != "quiet":
            report(
                "[2/3] Applying "
                f"{operation_count} experiment-apparatus operation(s)."
            )
        self.client.apply_tools(
            provisioning_profile,
            timeout_seconds=timeout_seconds,
            output_mode=output_mode,
            output=report,
        )

        for plugin in stale_plugins:
            self._remove_plugin_destination(plugin.destination)

        source_sha256 = self._apparatus_source_sha256(
            "hermes",
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
                    "toolsets": ",".join(plugin.toolsets),
                }
                for plugin in self.profile.plugins
            ],
            package_provenance=self.deployment_provenance,
        )
        if output_mode != "quiet":
            report("[3/3] Hermes experiment apparatus applied successfully.")

        return {
            "experiment_id": experiment_id,
            "source_sha256": source_sha256,
            "application_scope": application_scope,
            "plugins_synchronized": synchronized,
            "tool_search_enabled": self.profile.experiment_isolation.tool_search_enabled,
            "provisioning_operations": operation_count,
        }

    def apply_tools(
        self,
        experiment_id: str | None,
        *,
        timeout_seconds: float = 120.0,
        progress: Callable[[str], None] | None = None,
        output_mode: Literal["quiet", "normal", "verbose"] = "normal",
    ) -> dict[str, Any]:
        if experiment_id is not None:
            return self.apply_experiment(
                experiment_id,
                application_scope="tools",
                timeout_seconds=timeout_seconds,
                progress=progress,
                output_mode=output_mode,
            )

        if timeout_seconds <= 0:
            raise infrastructure_error(
                "Hermes apply-tools timeout must be greater than zero seconds."
            )

        report = progress if progress is not None else lambda message: None
        self.initialize_deployment()
        if self.docker.container_running():
            raise infrastructure_error(
                "Hermes gateway is running. Run 'acmm-infra hermes down' before "
                "applying baseline tool policy."
            )
        self._validate_existing_config_version()

        previous_state = read_experiment_apparatus_state(self.deployment_root)
        stale_plugins = self._stale_plugins(previous_state, desired_plugins=set())
        provisioning_profile = replace(self.profile, plugins=tuple(stale_plugins))

        if output_mode != "quiet":
            report("[1/2] Reconciling Hermes baseline tool policy.")
        operation_count = self.client.tool_operation_count(provisioning_profile)
        self.client.apply_tools(
            provisioning_profile,
            timeout_seconds=timeout_seconds,
            output_mode=output_mode,
            output=report,
        )

        for plugin in stale_plugins:
            self._remove_plugin_destination(plugin.destination)
        clear_experiment_apparatus_state(self.deployment_root)

        if output_mode != "quiet":
            report("[2/2] Hermes baseline tool policy applied successfully.")

        return {
            "experiment_id": None,
            "application_scope": "tools",
            "plugins_synchronized": [],
            "provisioning_operations": operation_count,
        }


    def status(self) -> dict[str, Any]:
        running, container_image = self.docker.container_info()

        observed: dict[str, Any] = {
            "container_running": running,
            "container_image": container_image,
            "hermes_version": None,
            "model_default": None,
            "model_provider": None,
            "model_base_url": None,
            "model_api_mode": None,
            "context_window": None,
            "reasoning_effort": None,
            "terminal_backend": None,
            "platform_toolsets_cli": None,
            "disabled_toolsets": None,
            "expected_disabled_toolsets": None,
            "max_turns": None,
            "compression_enabled": None,
            "memory_enabled": None,
            "user_profile_enabled": None,
            "checkpoints_enabled": None,
            "smart_model_routing_enabled": None,
            "tool_progress": None,
            "title_generation_enabled": None,
            "background_review_enabled": None,
            "tool_search_enabled": None,
            "bundled_skills_opt_out": None,
            "bundled_skill_manifest": None,
            "essential_skills": None,
            "live_observation_ok": False,
            "live_observation_error": None,
            "config_path": None,
            "env_path": None,
            "plugins_enabled": [],
            "plugin_tool_overrides": {},
            "provider_model_available": None,
            "provider_model_ids": [],
            "provider_model_error": None,
        }

        if running:
            observation = self.client.observe(
                self.profile,
                timeout_seconds=30.0,
            )

            if observation.succeeded:
                observed.update(observation.values)
                observed["live_observation_ok"] = True
            else:
                observed["live_observation_error"] = observation.error

        plugin_status = []

        for plugin in self.profile.plugins:
            source = self._plugin_source(plugin)
            destination = self.data_root / plugin.destination
            source_hash = _tree_sha256(source) if source.exists() else None
            destination_hash = _tree_sha256(destination) if destination.exists() else None

            override_observed = observed["plugin_tool_overrides"].get(
                plugin.name
            )

            plugin_status.append(
                {
                    "name": plugin.name,
                    "enabled_desired": plugin.enabled,
                    "enabled_observed": plugin.name in observed["plugins_enabled"],
                    "allow_tool_override_desired": plugin.allow_tool_override,
                    "allow_tool_override_observed": override_observed,
                    "source": str(plugin.source),
                    "destination": str(plugin.destination),
                    "source_sha256": source_hash,
                    "destination_sha256": destination_hash,
                    "synchronized": (
                        source_hash is not None
                        and source_hash == destination_hash
                    ),
                }
            )

        default_compose = self.defaults_root / "compose.yaml"
        deployed_compose = self.deployment_root / "compose.yaml"
        deployed_env = self.deployment_root / ".env"

        default_compose_hash = (
            _file_sha256(default_compose)
            if default_compose.is_file()
            else None
        )
        deployed_compose_hash = (
            _file_sha256(deployed_compose)
            if deployed_compose.is_file()
            else None
        )

        return {
            "runtime_root": str(self.runtime_root),
            "deployment_root": str(self.deployment_root),
            "experiment_apparatus": read_experiment_apparatus_state(
                self.deployment_root
            ),
            "data_root": str(self.data_root),
            "deployment": {
                "compose_path": str(deployed_compose),
                "compose_exists": deployed_compose.is_file(),
                "compose_default_sha256": default_compose_hash,
                "compose_deployed_sha256": deployed_compose_hash,
                "compose_matches_default": (
                    default_compose_hash is not None
                    and default_compose_hash == deployed_compose_hash
                ),
                "env_path": str(deployed_env),
                "env_exists": deployed_env.is_file(),
            },
            "desired": {
                "hermes_version": self.profile.expected_version,
                "container_image": self.profile.image,
                "container_name": self.profile.container_name,
                "model_provider": self.profile.model_provider,
                "model_base_url": self.profile.model_base_url,
                "model_api_mode": self.profile.model_api_mode,
                "model_default": self.profile.model_default,
                "context_window": self.profile.context_window,
                "reasoning_effort": self.profile.reasoning_effort,
                "terminal_backend": self.profile.terminal_backend,
                "experiment_isolation": {
                    "title_generation_enabled": (
                        self.profile.experiment_isolation.title_generation_enabled
                    ),
                    "background_review_enabled": (
                        self.profile.experiment_isolation.background_review_enabled
                    ),
                    "tool_search_enabled": (
                        self.profile.experiment_isolation.tool_search_enabled
                    ),
                },
                "blank_slate": {
                    "enabled": self.profile.blank_slate.enabled,
                    "minimal_toolsets": list(
                        self.profile.blank_slate.minimal_toolsets
                    ),
                    "max_turns": self.profile.blank_slate.max_turns,
                    "compression_enabled": (
                        self.profile.blank_slate.compression_enabled
                    ),
                    "memory_enabled": self.profile.blank_slate.memory_enabled,
                    "user_profile_enabled": (
                        self.profile.blank_slate.user_profile_enabled
                    ),
                    "checkpoints_enabled": (
                        self.profile.blank_slate.checkpoints_enabled
                    ),
                    "smart_model_routing_enabled": (
                        self.profile.blank_slate.smart_model_routing_enabled
                    ),
                    "tool_progress": self.profile.blank_slate.tool_progress,
                    "bundled_skills": self.profile.blank_slate.bundled_skills,
                    "overlay_toolsets": list(
                        managed_overlay_toolsets(self.profile.plugins)
                    ),
                },
            },
            "observed": observed,
            "plugins": plugin_status,
        }

    def doctor(self) -> doctor_report:
        status = self.status()
        observed = status["observed"]
        desired = status["desired"]

        docker_version = self.docker.docker_version()
        compose_version = self.docker.compose_version()
        compose_config = self.docker.compose_config()

        checks: list[doctor_check] = [
            doctor_check(
                name="docker",
                ok=docker_version.succeeded,
                detail=_result_detail(docker_version),
            ),
            doctor_check(
                name="docker_compose",
                ok=compose_version.succeeded,
                detail=_result_detail(compose_version),
            ),
            doctor_check(
                name="compose_config",
                ok=compose_config.succeeded,
                detail=(
                    "Compose configuration is valid."
                    if compose_config.succeeded
                    else _result_detail(compose_config)
                ),
            ),
            doctor_check(
                name="deployment_compose",
                ok=bool(status["deployment"]["compose_exists"]),
                detail=status["deployment"]["compose_path"],
            ),
            doctor_check(
                name="deployment_env",
                ok=bool(status["deployment"]["env_exists"]),
                detail=status["deployment"]["env_path"],
            ),
            doctor_check(
                name="compose_default",
                ok=bool(status["deployment"]["compose_matches_default"]),
                detail=(
                    f"default={status['deployment']['compose_default_sha256']}, "
                    f"deployed={status['deployment']['compose_deployed_sha256']}"
                ),
            ),
            doctor_check(
                name="runtime_data",
                ok=self.data_root.is_dir(),
                detail=str(self.data_root),
            ),
        ]

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
                "hermes",
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

        for plugin in status["plugins"]:
            checks.append(
                doctor_check(
                    name=f"plugin_sync:{plugin['name']}",
                    ok=bool(plugin["synchronized"]),
                    detail=(
                        f"source={plugin['source_sha256']}, "
                        f"deployed={plugin['destination_sha256']}"
                    ),
                )
            )

        if not observed["container_running"]:
            checks.append(
                doctor_check(
                    name="container_running",
                    ok=False,
                    detail=(
                        "Hermes container is not running. Run "
                        "'acmm-infra hermes up' before live readiness checks."
                    ),
                )
            )
            return doctor_report(checks=tuple(checks))

        checks.append(
            doctor_check(
                name="container_running",
                ok=True,
                detail="Hermes container is running.",
            )
        )

        if not observed["live_observation_ok"]:
            checks.append(
                doctor_check(
                    name="live_observation",
                    ok=False,
                    detail=(
                        observed["live_observation_error"]
                        or "Hermes live state could not be inspected."
                    ),
                )
            )
            return doctor_report(checks=tuple(checks))

        checks.extend(
            [
                doctor_check(
                    name="live_observation",
                    ok=True,
                    detail="Hermes live state was read successfully.",
                ),
                doctor_check(
                    name="container_image",
                    ok=observed["container_image"] == desired["container_image"],
                    detail=(
                        f"observed={observed['container_image']!r}, "
                        f"expected={desired['container_image']!r}"
                    ),
                ),
                doctor_check(
                    name="hermes_version",
                    ok=_version_matches(
                        observed["hermes_version"],
                        desired["hermes_version"],
                    ),
                    detail=(
                        f"observed={observed['hermes_version']!r}, "
                        f"expected={desired['hermes_version']!r}"
                    ),
                ),
                doctor_check(
                    name="model_provider",
                    ok=observed["model_provider"] == desired["model_provider"],
                    detail=(
                        f"observed={observed['model_provider']!r}, "
                        f"expected={desired['model_provider']!r}"
                    ),
                ),
                doctor_check(
                    name="model_base_url",
                    ok=observed["model_base_url"] == desired["model_base_url"],
                    detail=(
                        f"observed={observed['model_base_url']!r}, "
                        f"expected={desired['model_base_url']!r}"
                    ),
                ),
                doctor_check(
                    name="model_api_mode",
                    ok=observed["model_api_mode"] == desired["model_api_mode"],
                    detail=(
                        f"observed={observed['model_api_mode']!r}, "
                        f"expected={desired['model_api_mode']!r}"
                    ),
                ),
                doctor_check(
                    name="model_context_length",
                    ok=observed["context_window"] == desired["context_window"],
                    detail=(
                        f"observed={observed['context_window']!r}, "
                        f"expected={desired['context_window']!r}"
                    ),
                ),
                doctor_check(
                    name="model_default",
                    ok=observed["model_default"] == desired["model_default"],
                    detail=(
                        f"observed={observed['model_default']!r}, "
                        f"expected={desired['model_default']!r}"
                    ),
                ),
                doctor_check(
                    name="provider_model_available",
                    ok=observed["provider_model_available"] is True,
                    detail=_provider_model_availability_detail(
                        desired["model_default"],
                        observed["provider_model_available"],
                        observed["provider_model_ids"],
                        observed["provider_model_error"],
                    ),
                ),
                doctor_check(
                    name="reasoning_effort",
                    ok=observed["reasoning_effort"] == desired["reasoning_effort"],
                    detail=(
                        f"observed={observed['reasoning_effort']!r}, "
                        f"expected={desired['reasoning_effort']!r}"
                    ),
                ),
                doctor_check(
                    name="terminal_backend",
                    ok=observed["terminal_backend"] == desired["terminal_backend"],
                    detail=(
                        f"observed={observed['terminal_backend']!r}, "
                        f"expected={desired['terminal_backend']!r}"
                    ),
                ),
            ]
        )

        isolation = desired["experiment_isolation"]
        checks.extend(
            [
                doctor_check(
                    name="experiment_title_generation",
                    ok=(
                        observed["title_generation_enabled"]
                        == isolation["title_generation_enabled"]
                    ),
                    detail=(
                        f"observed={observed['title_generation_enabled']!r}, "
                        f"expected={isolation['title_generation_enabled']!r}"
                    ),
                ),
                doctor_check(
                    name="experiment_background_review",
                    ok=(
                        observed["background_review_enabled"]
                        == isolation["background_review_enabled"]
                    ),
                    detail=(
                        f"observed={observed['background_review_enabled']!r}, "
                        f"expected={isolation['background_review_enabled']!r}"
                    ),
                ),
                doctor_check(
                    name="experiment_tool_search",
                    ok=(
                        observed["tool_search_enabled"]
                        == isolation["tool_search_enabled"]
                    ),
                    detail=(
                        f"observed={observed['tool_search_enabled']!r}, "
                        f"expected={isolation['tool_search_enabled']!r}"
                    ),
                ),
            ]
        )

        if desired["blank_slate"]["enabled"]:
            blank = desired["blank_slate"]
            disabled = observed["disabled_toolsets"]
            expected_disabled = observed["expected_disabled_toolsets"]
            overlays = set(blank["overlay_toolsets"])

            checks.extend(
                [
                    doctor_check(
                        name="blank_slate_toolsets",
                        ok=(
                            observed["platform_toolsets_cli"]
                            == blank["minimal_toolsets"]
                        ),
                        detail=(
                            f"observed={observed['platform_toolsets_cli']!r}, "
                            f"expected={blank['minimal_toolsets']!r}"
                        ),
                    ),
                    doctor_check(
                        name="blank_slate_disabled_toolsets",
                        ok=(
                            isinstance(disabled, list)
                            and disabled == expected_disabled
                        ),
                        detail=(
                            f"observed={disabled!r}, "
                            f"expected={expected_disabled!r}"
                        ),
                    ),
                    doctor_check(
                        name="blank_slate_overlay_available",
                        ok=(
                            isinstance(disabled, list)
                            and overlays.isdisjoint(disabled)
                        ),
                        detail=(
                            f"overlay={sorted(overlays)!r}, "
                            f"disabled={disabled!r}"
                        ),
                    ),
                    doctor_check(
                        name="blank_slate_max_turns",
                        ok=observed["max_turns"] == blank["max_turns"],
                        detail=(
                            f"observed={observed['max_turns']!r}, "
                            f"expected={blank['max_turns']!r}"
                        ),
                    ),
                    doctor_check(
                        name="blank_slate_compression",
                        ok=(
                            observed["compression_enabled"]
                            == blank["compression_enabled"]
                        ),
                        detail=(
                            f"observed={observed['compression_enabled']!r}, "
                            f"expected={blank['compression_enabled']!r}"
                        ),
                    ),
                    doctor_check(
                        name="blank_slate_memory",
                        ok=observed["memory_enabled"] == blank["memory_enabled"],
                        detail=(
                            f"observed={observed['memory_enabled']!r}, "
                            f"expected={blank['memory_enabled']!r}"
                        ),
                    ),
                    doctor_check(
                        name="blank_slate_user_profile",
                        ok=(
                            observed["user_profile_enabled"]
                            == blank["user_profile_enabled"]
                        ),
                        detail=(
                            f"observed={observed['user_profile_enabled']!r}, "
                            f"expected={blank['user_profile_enabled']!r}"
                        ),
                    ),
                    doctor_check(
                        name="blank_slate_checkpoints",
                        ok=(
                            observed["checkpoints_enabled"]
                            == blank["checkpoints_enabled"]
                        ),
                        detail=(
                            f"observed={observed['checkpoints_enabled']!r}, "
                            f"expected={blank['checkpoints_enabled']!r}"
                        ),
                    ),
                    doctor_check(
                        name="blank_slate_smart_routing",
                        ok=(
                            observed["smart_model_routing_enabled"]
                            == blank["smart_model_routing_enabled"]
                        ),
                        detail=(
                            f"observed={observed['smart_model_routing_enabled']!r}, "
                            f"expected={blank['smart_model_routing_enabled']!r}"
                        ),
                    ),
                    doctor_check(
                        name="blank_slate_tool_progress",
                        ok=observed["tool_progress"] == blank["tool_progress"],
                        detail=(
                            f"observed={observed['tool_progress']!r}, "
                            f"expected={blank['tool_progress']!r}"
                        ),
                    ),
                    doctor_check(
                        name="blank_slate_bundled_skills_opt_out",
                        ok=observed["bundled_skills_opt_out"] is True,
                        detail=(
                            f"observed={observed['bundled_skills_opt_out']!r}, "
                            "expected=True"
                        ),
                    ),
                    doctor_check(
                        name="blank_slate_essential_skills",
                        ok=(
                            isinstance(observed["bundled_skill_manifest"], list)
                            and observed["bundled_skill_manifest"]
                            == observed["essential_skills"]
                        ),
                        detail=(
                            f"bundled={observed['bundled_skill_manifest']!r}, "
                            f"essential={observed['essential_skills']!r}"
                        ),
                    ),
                ]
            )

        for plugin in status["plugins"]:
            checks.append(
                doctor_check(
                    name=f"plugin_enabled:{plugin['name']}",
                    ok=(
                        plugin["enabled_observed"]
                        == plugin["enabled_desired"]
                    ),
                    detail=(
                        f"observed={plugin['enabled_observed']}, "
                        f"expected={plugin['enabled_desired']}"
                    ),
                )
            )
            checks.append(
                doctor_check(
                    name=f"plugin_tool_override:{plugin['name']}",
                    ok=(
                        plugin["allow_tool_override_observed"]
                        == plugin["allow_tool_override_desired"]
                    ),
                    detail=(
                        f"observed={plugin['allow_tool_override_observed']}, "
                        f"expected={plugin['allow_tool_override_desired']}"
                    ),
                )
            )

        return doctor_report(checks=tuple(checks))

    def adopt_data(self, source: str | Path) -> Path:
        if self.docker.container_running():
            raise infrastructure_error(
                "Hermes container is running. Run 'acmm-infra hermes down' before adoption."
            )

        source_path = Path(source).expanduser().resolve()

        if not source_path.is_dir():
            raise infrastructure_error(
                f"Existing Hermes data directory does not exist: '{source_path}'."
            )

        if (
            source_path == self.data_root
            or self.data_root.is_relative_to(source_path)
            or source_path.is_relative_to(self.data_root)
        ):
            raise infrastructure_error(
                "Existing Hermes data source and new data root cannot contain each other."
            )

        if self.data_root.exists() and _directory_has_payload(self.data_root):
            raise infrastructure_error(
                f"New Hermes data root is not empty: '{self.data_root}'."
            )

        self.data_root.parent.mkdir(parents=True, exist_ok=True)

        temp_parent = Path(
            tempfile.mkdtemp(
                prefix=".hermes-adopt-",
                dir=self.data_root.parent,
            )
        )
        staged = temp_parent / "data"

        try:
            shutil.copytree(source_path, staged, symlinks=True)

            if self.data_root.exists():
                shutil.rmtree(self.data_root)

            staged.rename(self.data_root)
        except Exception:
            raise
        finally:
            shutil.rmtree(temp_parent, ignore_errors=True)

        return self.data_root

    def reset(self, scope: str) -> tuple[Path, ...]:
        if self.docker.container_running():
            raise infrastructure_error(
                "Hermes container is running. Run 'acmm-infra hermes down' before reset."
            )

        paths = self.profile.reset_scopes.get(scope)

        if paths is None:
            available = ", ".join(sorted(self.profile.reset_scopes))
            raise infrastructure_error(
                f"Unknown reset scope '{scope}'. Available scopes: {available}."
            )

        removed: list[Path] = []

        for relative in paths:
            target = _safe_runtime_target(self.data_root, relative)

            if not target.exists() and not target.is_symlink():
                continue

            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)

            removed.append(target)

        return tuple(removed)

    def _validate_existing_config_version(self) -> None:
        config_path = self.data_root / "config.yaml"

        if not config_path.is_file():
            return

        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise infrastructure_error(
                f"Could not inspect existing Hermes config '{config_path}': {exc}"
            ) from exc

        if data is None:
            return

        if not isinstance(data, dict):
            raise infrastructure_error(
                f"Existing Hermes config '{config_path}' must be a YAML mapping."
            )

        version = data.get("_config_version")

        if version is None:
            return

        if not isinstance(version, int) or isinstance(version, bool):
            raise infrastructure_error(
                "Existing Hermes config.yaml has an invalid _config_version."
            )

        if version < HERMES_CONFIG_SUPPORT_FLOOR:
            raise infrastructure_error(
                f"Existing Hermes config.yaml has schema version {version}, below "
                f"Hermes's supported migration floor {HERMES_CONFIG_SUPPORT_FLOOR}. "
                "For the fresh ACMM deployment, run "
                "'acmm-infra hermes reset configuration --yes' and retry apply. "
                "Back it up first if it contains state you want to preserve."
            )

    def _ensure_hermes_env(self) -> None:
        env_path = self.data_root / ".env"

        if not env_path.exists():
            env_path.touch(mode=0o600)

        try:
            env_path.chmod(0o600)
        except OSError as exc:
            raise infrastructure_error(
                f"Could not secure Hermes secret store '{env_path}': {exc}"
            ) from exc

    def _stale_plugins(
        self,
        state: dict[str, object] | None,
        *,
        desired_plugins: set[tuple[str, str]],
    ) -> list[managed_hermes_plugin]:
        stale: dict[tuple[str, str], managed_hermes_plugin] = {}

        def add_stale(
            name: str,
            destination: str,
            toolsets: tuple[str, ...],
        ) -> None:
            key = (name, destination)
            if key in desired_plugins:
                return
            existing = stale.get(key)
            if existing is not None:
                toolsets = tuple(dict.fromkeys(existing.toolsets + toolsets))
            stale[key] = managed_hermes_plugin(
                name=name,
                source=Path("."),
                destination=Path(destination),
                enabled=False,
                allow_tool_override=False,
                toolsets=toolsets,
            )

        if state is not None:
            resources = state.get("resources")
            if isinstance(resources, list):
                for resource in resources:
                    if (
                        not isinstance(resource, dict)
                        or resource.get("kind") != "plugin"
                    ):
                        continue
                    name = resource.get("name")
                    destination = resource.get("destination")
                    toolsets_value = resource.get("toolsets", "")
                    if (
                        not isinstance(name, str)
                        or not isinstance(destination, str)
                        or not isinstance(toolsets_value, str)
                    ):
                        continue
                    add_stale(
                        name,
                        destination,
                        tuple(item for item in toolsets_value.split(",") if item),
                    )

        for experiment_id in registered_experiment_apparatus_ids(
            self.repo_root,
            "hermes",
        ):
            profile = with_hermes_experiment(
                self.repo_root,
                self.profile,
                experiment_id,
            )
            for plugin in profile.plugins:
                deployed = _safe_runtime_target(
                    self.data_root,
                    plugin.destination,
                )
                if deployed.exists():
                    add_stale(
                        plugin.name,
                        plugin.destination.as_posix(),
                        plugin.toolsets,
                    )

        return list(stale.values())

    def _remove_plugin_destination(self, destination: Path) -> None:
        target = _safe_runtime_target(self.data_root, destination)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()

    def _plugin_source(self, plugin: managed_hermes_plugin) -> Path:
        root = (plugin.source_root or self.repo_root).resolve()
        source = (root / plugin.source).resolve()
        if not source.is_relative_to(root):
            raise infrastructure_error(
                f"Managed Hermes plugin source escapes its source root: '{source}'."
            )
        return source

    def _sync_plugin(self, plugin: managed_hermes_plugin) -> None:
        source = self._plugin_source(plugin)
        destination = _safe_runtime_target(self.data_root, plugin.destination)

        if not source.is_dir():
            raise infrastructure_error(
                f"Managed plugin source does not exist: '{source}'."
            )

        destination.parent.mkdir(parents=True, exist_ok=True)

        temp_root = Path(
            tempfile.mkdtemp(
                prefix=f".{plugin.name}.sync-",
                dir=destination.parent,
            )
        )
        staged = temp_root / plugin.name
        backup = destination.parent / f".{plugin.name}.backup"

        try:
            shutil.copytree(
                source,
                staged,
                ignore=copytree_ignore_generated,
            )

            if backup.exists():
                shutil.rmtree(backup)

            if destination.exists():
                destination.rename(backup)

            staged.rename(destination)

            if backup.exists():
                shutil.rmtree(backup)
        except Exception:
            if not destination.exists() and backup.exists():
                backup.rename(destination)
            raise
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
            if backup.exists() and destination.exists():
                shutil.rmtree(backup, ignore_errors=True)


def _directory_has_payload(path: Path) -> bool:
    return any(
        item.is_file() or item.is_symlink()
        for item in path.rglob("*")
    )


def _safe_runtime_target(
    data_root: Path,
    relative: Path,
) -> Path:
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise infrastructure_error(f"Unsafe runtime-relative path: '{relative}'.")

    target = data_root.joinpath(*relative.parts)
    resolved_parent = target.parent.resolve()

    if not resolved_parent.is_relative_to(data_root.resolve()):
        raise infrastructure_error(f"Runtime path escapes data root: '{relative}'.")

    return target


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    for item in iter_managed_source_files(path):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)

        content = item.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)

    return digest.hexdigest()


def _parse_json_value(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _parse_json_scalar(stdout: str) -> Any:
    return _parse_json_value(stdout)


def _provider_model_availability_detail(
    model_name: str,
    available: object,
    model_ids: object,
    error: object,
) -> str:
    if available is True:
        return f"model {model_name!r} is available from the configured Ollama host"
    if isinstance(error, str) and error:
        return (
            f"model {model_name!r} could not be verified from the configured "
            f"Ollama host: {error}"
        )
    count = len(model_ids) if isinstance(model_ids, list) else 0
    return (
        f"model {model_name!r} is not registered on the configured Ollama host; "
        f"observed {count} models. Run 'ollama pull {model_name}' on the Ollama "
        "host before qualification."
    )


def _version_matches(observed: Any, expected: str) -> bool:
    if not isinstance(observed, str):
        return False

    return expected in observed


def _result_detail(result: Any) -> str:
    return (
        result.stdout.strip()
        or result.stderr.strip()
        or f"exit status {result.returncode}"
    )
