from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from acmm_infra.profiles.common import (
    _boolean,
    _nonempty_string,
    _safe_relative_path,
    _string_tuple,
    profile_error,
)
from acmm_infra.profiles.llm import load_llm_baseline


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
    llm_baseline_id: str
    llm_logical_provider: str
    llm_minimum_context_window: int
    model_provider: str
    model_base_url: str
    model_api_mode: str
    model_default: str
    context_window: int
    reasoning_effort: str
    terminal_backend: str
    blank_slate: blank_slate_profile
    experiment_isolation: experiment_isolation_profile
    reset_scopes: dict[str, tuple[Path, ...]]
    managed_config_overrides: tuple[tuple[str, str], ...] = ()

def load_hermes_profile(
    repo_root: str | Path,
    llm_baseline_id: str | None = None,
) -> hermes_profile:
    root = Path(repo_root).resolve()
    llm = load_llm_baseline(root, llm_baseline_id)
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
        reset_data = data["reset_scopes"]
    except (KeyError, TypeError) as exc:
        raise profile_error(f"Hermes profile is missing required field: {exc}") from exc

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
    if not isinstance(gateway_port, int) or isinstance(gateway_port, bool):
        raise profile_error("container.gateway_port must be an integer.")

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
        llm_baseline_id=llm.id,
        llm_logical_provider=llm.logical_provider,
        llm_minimum_context_window=llm.minimum_context_window,
        model_provider=llm.hermes.model_provider,
        model_base_url=llm.hermes.model_base_url,
        model_api_mode=llm.hermes.model_api_mode,
        model_default=llm.model_name,
        context_window=llm.hermes.requested_context_window,
        reasoning_effort=llm.hermes.reasoning_effort,
        terminal_backend=_nonempty_string(
            baseline.get("terminal_backend"),
            "baseline.terminal_backend",
        ),
        blank_slate=blank_slate,
        experiment_isolation=experiment_isolation,
        reset_scopes=reset_scopes,
    )

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
