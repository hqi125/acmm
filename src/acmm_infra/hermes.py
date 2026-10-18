from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import shlex
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Literal

import yaml

from acmm_infra.docker import docker_client, require_success
from acmm_infra.profile import hermes_profile, managed_plugin


HERMES_CLI = "/opt/hermes/bin/hermes"
HERMES_PYTHON = "/opt/hermes/.venv/bin/python"
HERMES_CONFIG_SUPPORT_FLOOR = 12


class infrastructure_error(RuntimeError):
    """Raised when infrastructure state cannot be safely managed."""


@dataclass(frozen=True)
class doctor_check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class doctor_report:
    checks: tuple[doctor_check, ...]

    @property
    def is_ready(self) -> bool:
        return all(check.ok for check in self.checks)


class hermes_manager:
    def __init__(
        self,
        repo_root: str | Path,
        runtime_root: str | Path,
        profile: hermes_profile,
        docker: docker_client,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.profile = profile
        self.docker = docker
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

    def init_runtime(self) -> None:
        self.deployment_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.data_root / "plugins").mkdir(parents=True, exist_ok=True)
        self._ensure_hermes_env()

    def initialize_deployment(self) -> dict[str, str]:
        self.init_runtime()

        compose_state = self._install_default_if_missing(
            self.defaults_root / "compose.yaml",
            self.deployment_root / "compose.yaml",
        )
        env_state = self._install_default_if_missing(
            self.defaults_root / "env.defaults",
            self.deployment_root / ".env",
        )

        return {
            "compose": compose_state,
            "env": env_state,
            "deployment_root": str(self.deployment_root),
            "data_root": str(self.data_root),
        }

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

        if output_mode != "quiet":
            report("[1/4] Verifying initialized Hermes deployment.")
        self.initialize_deployment()

        if self.docker.container_running():
            raise infrastructure_error(
                "Hermes gateway is running. Run 'acmm-infra hermes down' before "
                "applying infrastructure configuration."
            )

        self._validate_existing_config_version()

        if output_mode != "quiet":
            report("[2/4] Synchronizing repository-managed Hermes plugins.")
        synchronized: list[str] = []

        for plugin in self.profile.plugins:
            self._sync_plugin(plugin)
            synchronized.append(plugin.name)

        settings = (
            ("model.provider", self.profile.model_provider),
            ("model.base_url", self.profile.model_base_url),
            ("model.api_mode", self.profile.model_api_mode),
            ("model.context_length", str(self.profile.context_window)),
            ("model.default", self.profile.model_default),
            ("agent.reasoning_effort", self.profile.reasoning_effort),
            ("terminal.backend", self.profile.terminal_backend),
        )

        extra_operations = 3 if self.profile.blank_slate.enabled else 0
        operation_count = (
            len(settings)
            + len(self.profile.plugins)
            + extra_operations
        )
        script = _build_provisioning_script(
            settings=settings,
            plugins=self.profile.plugins,
            profile=self.profile,
        )

        if output_mode != "quiet":
            report(
                "[3/4] Applying "
                f"{operation_count} Hermes operations in one provisioning container "
                f"(timeout {timeout_seconds:g}s)."
            )

        result = self.docker.provision(
            script,
            timeout_seconds=timeout_seconds,
            output_mode=output_mode,
            output=report,
        )

        require_success(
            result,
            "Hermes batched provisioning",
        )

        if output_mode != "quiet":
            report("[4/4] Hermes infrastructure baseline applied successfully.")

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
            "plugins_synchronized": synchronized,
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
            "bundled_skills_opt_out": None,
            "config_path": None,
            "env_path": None,
            "plugins_enabled": [],
            "plugin_tool_overrides": {},
        }

        if running:
            observation_result = self.docker.exec_script(
                _build_observation_script(self.profile),
                timeout_seconds=30.0,
            )

            if observation_result.succeeded:
                observed.update(
                    _parse_observation_output(
                        observation_result.stdout,
                        self.profile.plugins,
                    )
                )

        plugin_status = []

        for plugin in self.profile.plugins:
            source = self.repo_root / plugin.source
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
                        _managed_overlay_toolsets(self.profile.plugins)
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
            doctor_check(
                name="container_running",
                ok=bool(observed["container_running"]),
                detail=(
                    "Hermes container is running."
                    if observed["container_running"]
                    else "Hermes container is not running."
                ),
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
                        ok=(
                            observed["memory_enabled"]
                            == blank["memory_enabled"]
                        ),
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
                            isinstance(
                                observed["bundled_skill_manifest"],
                                list,
                            )
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
                    name=f"plugin_sync:{plugin['name']}",
                    ok=bool(plugin["synchronized"]),
                    detail=(
                        f"source={plugin['source_sha256']}, "
                        f"deployed={plugin['destination_sha256']}"
                    ),
                )
            )
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

    def _install_default_if_missing(
        self,
        source: Path,
        destination: Path,
    ) -> str:
        if not source.is_file():
            raise infrastructure_error(
                f"Shipped infrastructure default does not exist: '{source}'."
            )

        if destination.exists():
            return "preserved"

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return "created"

    def _sync_plugin(self, plugin: managed_plugin) -> None:
        source = (self.repo_root / plugin.source).resolve()
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
            shutil.copytree(source, staged)

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



def _build_observation_script(
    profile: hermes_profile,
) -> str:
    commands = (
        ("hermes_version", [HERMES_CLI, "--version"]),
        (
            "model_provider",
            [HERMES_CLI, "config", "get", "model.provider", "--json"],
        ),
        (
            "model_base_url",
            [HERMES_CLI, "config", "get", "model.base_url", "--json"],
        ),
        (
            "model_api_mode",
            [HERMES_CLI, "config", "get", "model.api_mode", "--json"],
        ),
        (
            "context_window",
            [
                HERMES_CLI,
                "config",
                "get",
                "model.context_length",
                "--json",
            ],
        ),
        (
            "model_default",
            [HERMES_CLI, "config", "get", "model.default", "--json"],
        ),
        (
            "reasoning_effort",
            [
                HERMES_CLI,
                "config",
                "get",
                "agent.reasoning_effort",
                "--json",
            ],
        ),
        (
            "terminal_backend",
            [
                HERMES_CLI,
                "config",
                "get",
                "terminal.backend",
                "--json",
            ],
        ),
        (
            "platform_toolsets_cli",
            [
                HERMES_CLI,
                "config",
                "get",
                "platform_toolsets.cli",
                "--json",
            ],
        ),
        (
            "disabled_toolsets",
            [
                HERMES_CLI,
                "config",
                "get",
                "agent.disabled_toolsets",
                "--json",
            ],
        ),
        (
            "max_turns",
            [
                HERMES_CLI,
                "config",
                "get",
                "agent.max_turns",
                "--json",
            ],
        ),
        (
            "compression_enabled",
            [
                HERMES_CLI,
                "config",
                "get",
                "compression.enabled",
                "--json",
            ],
        ),
        (
            "memory_enabled",
            [
                HERMES_CLI,
                "config",
                "get",
                "memory.memory_enabled",
                "--json",
            ],
        ),
        (
            "user_profile_enabled",
            [
                HERMES_CLI,
                "config",
                "get",
                "memory.user_profile_enabled",
                "--json",
            ],
        ),
        (
            "checkpoints_enabled",
            [
                HERMES_CLI,
                "config",
                "get",
                "checkpoints.enabled",
                "--json",
            ],
        ),
        (
            "smart_model_routing_enabled",
            [
                HERMES_CLI,
                "config",
                "get",
                "smart_model_routing.enabled",
                "--json",
            ],
        ),
        (
            "tool_progress",
            [
                HERMES_CLI,
                "config",
                "get",
                "display.tool_progress",
                "--json",
            ],
        ),
        ("config_path", [HERMES_CLI, "config", "path"]),
        ("env_path", [HERMES_CLI, "config", "env-path"]),
        (
            "plugins_enabled",
            [HERMES_CLI, "config", "get", "plugins.enabled", "--json"],
        ),
    )

    plugin_commands = tuple(
        (
            f"plugin_override:{plugin.name}",
            [
                HERMES_CLI,
                "config",
                "get",
                f"plugins.entries.{plugin.name}.allow_tool_override",
                "--json",
            ],
        )
        for plugin in profile.plugins
    )

    expected_disabled_command = [
        HERMES_PYTHON,
        "-c",
        _blank_slate_expected_disabled_python(profile),
    ]
    marker_command = [
        "sh",
        "-c",
        (
            "if [ -f /opt/data/.no-bundled-skills ]; "
            "then printf true; else printf false; fi"
        ),
    ]
    bundled_manifest_command = [
        HERMES_PYTHON,
        "-c",
        (
            "import json; "
            "from tools.skills_sync import _read_manifest; "
            "print(json.dumps(sorted(_read_manifest())))"
        ),
    ]
    essential_skills_command = [
        HERMES_PYTHON,
        "-c",
        (
            "import json; "
            "from agent.skill_utils import ESSENTIAL_SKILLS; "
            "print(json.dumps(sorted(ESSENTIAL_SKILLS)))"
        ),
    ]

    commands = (
        commands
        + plugin_commands
        + (
            (
                "expected_disabled_toolsets",
                expected_disabled_command,
            ),
            (
                "bundled_skills_opt_out",
                marker_command,
            ),
            (
                "bundled_skill_manifest",
                bundled_manifest_command,
            ),
            (
                "essential_skills",
                essential_skills_command,
            ),
        )
    )

    lines = [
        "set -u",
        (
            "acmm_emit() { key=\"$1\"; shift; "
            "value=\"$(\"$@\" 2>/dev/null || true)\"; "
            "printf '__ACMM__%s=%s\\n' \"$key\" \"$value\"; }"
        ),
    ]

    for key, command in commands:
        lines.append(
            "acmm_emit "
            + shlex.quote(key)
            + " "
            + " ".join(shlex.quote(part) for part in command)
        )

    return "\n".join(lines)


def _parse_observation_output(
    stdout: str,
    managed_plugins: tuple[managed_plugin, ...],
) -> dict[str, Any]:
    raw: dict[str, str] = {}

    for line in stdout.splitlines():
        if not line.startswith("__ACMM__") or "=" not in line:
            continue

        key, value = line[len("__ACMM__"):].split("=", maxsplit=1)
        raw[key] = value

    observed: dict[str, Any] = {}

    scalar_keys = (
        "model_provider",
        "model_base_url",
        "model_api_mode",
        "context_window",
        "model_default",
        "reasoning_effort",
        "terminal_backend",
        "platform_toolsets_cli",
        "disabled_toolsets",
        "max_turns",
        "compression_enabled",
        "memory_enabled",
        "user_profile_enabled",
        "checkpoints_enabled",
        "smart_model_routing_enabled",
        "tool_progress",
        "expected_disabled_toolsets",
        "bundled_skills_opt_out",
        "bundled_skill_manifest",
        "essential_skills",
    )

    for key in scalar_keys:
        value = raw.get(key, "")
        observed[key] = _parse_json_scalar(value) if value else None

    observed["hermes_version"] = raw.get("hermes_version") or None
    observed["config_path"] = raw.get("config_path") or None
    observed["env_path"] = raw.get("env_path") or None

    plugins = _parse_json_value(raw.get("plugins_enabled", ""))
    observed["plugins_enabled"] = (
        [item for item in plugins if isinstance(item, str)]
        if isinstance(plugins, list)
        else []
    )

    observed["plugin_tool_overrides"] = {}

    for plugin in managed_plugins:
        raw_value = raw.get(f"plugin_override:{plugin.name}", "")
        value = _parse_json_scalar(raw_value) if raw_value else None
        observed["plugin_tool_overrides"][plugin.name] = (
            value if isinstance(value, bool) else None
        )

    return observed


def _build_provisioning_script(
    settings: tuple[tuple[str, str], ...],
    plugins: tuple[managed_plugin, ...],
    profile: hermes_profile,
) -> str:
    operations: list[tuple[str, str]] = []

    for key, value in settings:
        command = [
            HERMES_CLI,
            "config",
            "set",
            key,
            value,
            "--force",
        ]
        operations.append(
            (
                f"set {key}",
                " ".join(shlex.quote(part) for part in command),
            )
        )

    if profile.blank_slate.enabled:
        blank_slate_command = [
            HERMES_PYTHON,
            "-c",
            _blank_slate_apply_python(profile),
        ]
        operations.append(
            (
                "apply Blank Slate policy",
                " ".join(
                    shlex.quote(part)
                    for part in blank_slate_command
                ),
            )
        )

        opt_out_command = [
            HERMES_CLI,
            "skills",
            "opt-out",
            "--remove",
            "--yes",
        ]
        operations.append(
            (
                "remove non-essential bundled skills",
                " ".join(
                    shlex.quote(part)
                    for part in opt_out_command
                ),
            )
        )

        sync_command = [
            HERMES_PYTHON,
            "-c",
            _essential_skill_sync_python(),
        ]
        operations.append(
            (
                "restore essential bundled skills",
                " ".join(
                    shlex.quote(part)
                    for part in sync_command
                ),
            )
        )

    for plugin in plugins:
        action = "enable" if plugin.enabled else "disable"
        command = [
            HERMES_CLI,
            "plugins",
            action,
            plugin.name,
        ]

        if plugin.enabled:
            command.append(
                "--allow-tool-override"
                if plugin.allow_tool_override
                else "--no-allow-tool-override"
            )

        operations.append(
            (
                f"{action} plugin {plugin.name}",
                " ".join(shlex.quote(part) for part in command),
            )
        )

    lines = ["set -eu"]
    total = len(operations)

    for index, (description, command) in enumerate(
        operations,
        start=1,
    ):
        label = f"[Hermes {index}/{total}] {description}"
        error_label = (
            f"[Hermes ERROR] {index}/{total} {description} failed"
        )
        lines.append(f"printf '%s\\n' {shlex.quote(label)}")
        lines.append(f"if {command}; then")
        lines.append("    :")
        lines.append("else")
        lines.append("    status=$?")
        lines.append(
            "    printf '%s (exit=%s)\\n' "
            + shlex.quote(error_label)
            + ' "$status" >&2'
        )
        lines.append('    exit "$status"')
        lines.append("fi")

    lines.append(
        f"printf '%s\\n' "
        f"{shlex.quote('[Hermes] Provisioning batch complete.')}"
    )
    return "\n".join(lines)


def _blank_slate_apply_python(
    profile: hermes_profile,
) -> str:
    keep = sorted(profile.blank_slate.minimal_toolsets)
    overlay = sorted(_managed_overlay_toolsets(profile.plugins))

    return f"""
from hermes_cli.setup import load_config, save_config
from hermes_cli.setup_quick import (
    _blank_slate_minimal_toolsets,
    _blank_slate_minimize_config,
)

keep = set({keep!r})
overlay = set({overlay!r})
config = load_config()

_blank_slate_minimal_toolsets(config)
_blank_slate_minimize_config(config)

disabled = set(
    config.setdefault("agent", {{}}).get("disabled_toolsets") or []
)
disabled.difference_update(overlay)
config["agent"]["disabled_toolsets"] = sorted(disabled)

save_config(config)

actual_keep = set(
    config.setdefault("platform_toolsets", {{}}).get("cli") or []
)

if actual_keep != keep:
    raise RuntimeError(
        "Pinned Blank Slate helper produced unexpected CLI toolsets: "
        + repr(sorted(actual_keep))
    )

print(
    "✓ Blank Slate policy applied: "
    + ",".join(sorted(actual_keep))
    + "; explicit overlays="
    + (",".join(sorted(overlay)) or "none")
)
""".strip()


def _blank_slate_expected_disabled_python(
    profile: hermes_profile,
) -> str:
    overlay = sorted(_managed_overlay_toolsets(profile.plugins))

    return f"""
import json
from hermes_cli.setup import load_config
from hermes_cli.setup_quick import _blank_slate_minimal_toolsets

overlay = set({overlay!r})
config = load_config()
_blank_slate_minimal_toolsets(config)

disabled = set(
    config.setdefault("agent", {{}}).get("disabled_toolsets") or []
)
disabled.difference_update(overlay)

print(json.dumps(sorted(disabled)))
""".strip()


def _essential_skill_sync_python() -> str:
    return """
from tools.skills_sync import sync_skills

result = sync_skills(quiet=True)
copied = len(result.get("copied", []))
updated = len(result.get("updated", []))
print(
    f"✓ Essential bundled skills synchronized "
    f"(copied={copied}, updated={updated})."
)
""".strip()


def _managed_overlay_toolsets(
    plugins: tuple[managed_plugin, ...],
) -> tuple[str, ...]:
    toolsets = {
        toolset
        for plugin in plugins
        if plugin.enabled
        for toolset in plugin.toolsets
    }
    return tuple(sorted(toolsets))


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

    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue

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
