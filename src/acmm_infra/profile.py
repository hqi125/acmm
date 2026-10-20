from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml


class profile_error(ValueError):
    """Raised when an ACMM infrastructure profile is invalid."""


@dataclass(frozen=True)
class managed_plugin:
    name: str
    source: Path
    destination: Path
    enabled: bool
    allow_tool_override: bool
    toolsets: tuple[str, ...]


@dataclass(frozen=True)
class blank_slate_profile:
    enabled: bool
    minimal_toolsets: tuple[str, ...]
    max_turns: int
    compression_enabled: bool
    memory_enabled: bool
    user_profile_enabled: bool
    checkpoints_enabled: bool
    smart_model_routing_enabled: bool
    tool_progress: str
    bundled_skills: str


@dataclass(frozen=True)
class experiment_isolation_profile:
    title_generation_enabled: bool
    background_review_enabled: bool
    tool_search_enabled: str


@dataclass(frozen=True)
class hermes_profile:
    expected_version: str
    service_name: str
    container_name: str
    image: str
    gateway_port: int
    deployment_directory: Path
    data_directory: Path
    model_provider: str
    model_base_url: str
    model_api_mode: str
    model_default: str
    context_window: int
    reasoning_effort: str
    terminal_backend: str
    blank_slate: blank_slate_profile
    experiment_isolation: experiment_isolation_profile
    plugins: tuple[managed_plugin, ...]
    reset_scopes: dict[str, tuple[Path, ...]]


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_runtime_root() -> Path:
    configured = os.getenv("ACMM_RUNTIME_ROOT")

    if configured:
        return Path(configured).expanduser()

    return Path.home() / "acmm_runtime"


def load_hermes_profile(
    repo_root: str | Path,
) -> hermes_profile:
    root = Path(repo_root).resolve()
    path = root / "infrastructure" / "hermes" / "profile.yaml"

    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as exc:
        raise profile_error(f"Could not read Hermes profile '{path}': {exc}") from exc
    except yaml.YAMLError as exc:
        raise profile_error(f"Could not parse Hermes profile '{path}': {exc}") from exc

    if not isinstance(data, dict):
        raise profile_error("Hermes profile must be a YAML mapping.")

    if data.get("schema_version") != "0.1":
        raise profile_error("Hermes profile schema_version must be '0.1'.")

    try:
        agent = data["agent"]
        container = data["container"]
        runtime = data["runtime"]
        baseline = data["baseline"]
        blank_slate_data = data["blank_slate"]
        isolation_data = data["experiment_isolation"]
        plugins_data = data["plugins"]["managed"]
        reset_data = data["reset_scopes"]
    except (KeyError, TypeError) as exc:
        raise profile_error(f"Hermes profile is missing required field: {exc}") from exc

    plugins: list[managed_plugin] = []

    for index, plugin in enumerate(plugins_data):
        if not isinstance(plugin, dict):
            raise profile_error(f"plugins.managed[{index}] must be a mapping.")

        source = _safe_relative_path(
            plugin.get("source"),
            f"plugins.managed[{index}].source",
        )
        destination = _safe_relative_path(
            plugin.get("destination"),
            f"plugins.managed[{index}].destination",
        )

        plugins.append(
            managed_plugin(
                name=_nonempty_string(
                    plugin.get("name"),
                    f"plugins.managed[{index}].name",
                ),
                source=source,
                destination=destination,
                enabled=bool(plugin.get("enabled", False)),
                allow_tool_override=_boolean(
                    plugin.get("allow_tool_override", False),
                    f"plugins.managed[{index}].allow_tool_override",
                ),
                toolsets=_string_tuple(
                    plugin.get("toolsets", []),
                    f"plugins.managed[{index}].toolsets",
                ),
            )
        )

    reset_scopes: dict[str, tuple[Path, ...]] = {}

    if not isinstance(reset_data, dict):
        raise profile_error("reset_scopes must be a mapping.")

    for scope_name, values in reset_data.items():
        if not isinstance(scope_name, str) or not scope_name:
            raise profile_error("reset scope names must be non-empty strings.")

        if not isinstance(values, list):
            raise profile_error(f"reset_scopes.{scope_name} must be a list.")

        reset_scopes[scope_name] = tuple(
            _safe_relative_path(
                value,
                f"reset_scopes.{scope_name}",
            )
            for value in values
        )

    gateway_port = container.get("gateway_port")
    context_window = baseline.get("context_window")
    if not isinstance(gateway_port, int) or isinstance(gateway_port, bool):
        raise profile_error("container.gateway_port must be an integer.")

    if not isinstance(context_window, int) or isinstance(context_window, bool):
        raise profile_error("baseline.context_window must be an integer.")

    blank_slate = _load_blank_slate_profile(blank_slate_data)
    experiment_isolation = _load_experiment_isolation_profile(
        isolation_data
    )

    return hermes_profile(
        expected_version=_nonempty_string(agent.get("expected_version"), "agent.expected_version"),
        service_name=_nonempty_string(container.get("service"), "container.service"),
        container_name=_nonempty_string(container.get("name"), "container.name"),
        image=_nonempty_string(container.get("image"), "container.image"),
        gateway_port=gateway_port,
        deployment_directory=_safe_relative_path(
            runtime.get("deployment_directory"),
            "runtime.deployment_directory",
        ),
        data_directory=_safe_relative_path(
            runtime.get("data_directory"),
            "runtime.data_directory",
        ),
        model_provider=_nonempty_string(
            baseline.get("model_provider"),
            "baseline.model_provider",
        ),
        model_base_url=_nonempty_string(
            baseline.get("model_base_url"),
            "baseline.model_base_url",
        ),
        model_api_mode=_nonempty_string(
            baseline.get("model_api_mode"),
            "baseline.model_api_mode",
        ),
        model_default=_nonempty_string(
            baseline.get("model_default"),
            "baseline.model_default",
        ),
        context_window=context_window,
        reasoning_effort=_nonempty_string(
            baseline.get("reasoning_effort"),
            "baseline.reasoning_effort",
        ),
        terminal_backend=_nonempty_string(
            baseline.get("terminal_backend"),
            "baseline.terminal_backend",
        ),
        blank_slate=blank_slate,
        experiment_isolation=experiment_isolation,
        plugins=tuple(plugins),
        reset_scopes=reset_scopes,
    )


def _nonempty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise profile_error(f"{path} must be a non-empty string.")

    return value.strip()


def _safe_relative_path(value: Any, path: str) -> Path:
    text = _nonempty_string(value, path)
    parsed = Path(text)

    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise profile_error(f"{path} must be a safe relative path.")

    return parsed


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise profile_error(f"{path} must be a boolean.")

    return value


def _load_blank_slate_profile(value: Any) -> blank_slate_profile:
    if not isinstance(value, dict):
        raise profile_error("blank_slate must be a mapping.")

    max_turns = value.get("max_turns")

    if (
        not isinstance(max_turns, int)
        or isinstance(max_turns, bool)
        or max_turns < 1
    ):
        raise profile_error(
            "blank_slate.max_turns must be a positive integer."
        )

    bundled_skills = _nonempty_string(
        value.get("bundled_skills"),
        "blank_slate.bundled_skills",
    )

    if bundled_skills != "essential_only":
        raise profile_error(
            "blank_slate.bundled_skills must currently be "
            "'essential_only'."
        )

    return blank_slate_profile(
        enabled=_boolean(
            value.get("enabled"),
            "blank_slate.enabled",
        ),
        minimal_toolsets=_string_tuple(
            value.get("minimal_toolsets"),
            "blank_slate.minimal_toolsets",
        ),
        max_turns=max_turns,
        compression_enabled=_boolean(
            value.get("compression_enabled"),
            "blank_slate.compression_enabled",
        ),
        memory_enabled=_boolean(
            value.get("memory_enabled"),
            "blank_slate.memory_enabled",
        ),
        user_profile_enabled=_boolean(
            value.get("user_profile_enabled"),
            "blank_slate.user_profile_enabled",
        ),
        checkpoints_enabled=_boolean(
            value.get("checkpoints_enabled"),
            "blank_slate.checkpoints_enabled",
        ),
        smart_model_routing_enabled=_boolean(
            value.get("smart_model_routing_enabled"),
            "blank_slate.smart_model_routing_enabled",
        ),
        tool_progress=_nonempty_string(
            value.get("tool_progress"),
            "blank_slate.tool_progress",
        ),
        bundled_skills=bundled_skills,
    )



def _load_experiment_isolation_profile(
    value: Any,
) -> experiment_isolation_profile:
    if not isinstance(value, dict):
        raise profile_error("experiment_isolation must be a mapping.")

    tool_search_enabled = _nonempty_string(
        value.get("tool_search_enabled"),
        "experiment_isolation.tool_search_enabled",
    )

    if tool_search_enabled not in {"auto", "on", "off"}:
        raise profile_error(
            "experiment_isolation.tool_search_enabled must be "
            "'auto', 'on', or 'off'."
        )

    return experiment_isolation_profile(
        title_generation_enabled=_boolean(
            value.get("title_generation_enabled"),
            "experiment_isolation.title_generation_enabled",
        ),
        background_review_enabled=_boolean(
            value.get("background_review_enabled"),
            "experiment_isolation.background_review_enabled",
        ),
        tool_search_enabled=tool_search_enabled,
    )


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise profile_error(f"{path} must be a list.")

    items = tuple(
        _nonempty_string(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    )

    if len(items) != len(set(items)):
        raise profile_error(f"{path} must not contain duplicates.")

    return items
