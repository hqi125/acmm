from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from acmm_infra.profiles.common import (
    _boolean,
    _nonempty_string,
    _safe_relative_path,
    profile_error,
)
from acmm_infra.profiles.llm import load_llm_baseline


@dataclass(frozen=True)
class open_webui_isolation_profile:
    direct_connections_enabled: bool
    direct_integrations_enabled: bool
    web_search_enabled: bool
    code_execution_enabled: bool
    code_interpreter_enabled: bool
    memories_enabled: bool
    memory_system_context_enabled: bool
    memory_background_review_enabled: bool
    image_generation_enabled: bool
    image_prompt_generation_enabled: bool
    title_generation_enabled: bool
    tags_generation_enabled: bool
    follow_up_generation_enabled: bool
    autocomplete_generation_enabled: bool
    search_query_generation_enabled: bool
    retrieval_query_generation_enabled: bool
    external_tool_servers_enabled: bool
    terminal_servers_enabled: bool


@dataclass(frozen=True)
class open_webui_profile:
    expected_version: str
    service_name: str
    container_name: str
    image: str
    web_port: int
    deployment_directory: Path
    data_directory: Path
    llm_baseline_id: str
    llm_logical_provider: str
    llm_minimum_context_window: int
    llm_requested_context_window: int | None
    ollama_base_url: str
    model_default: str
    webui_auth: bool
    secret_key_file: str
    ollama_api_enabled: bool
    openai_api_enabled: bool
    plugins_enabled: bool
    pip_install_frontmatter_requirements_enabled: bool
    telemetry_enabled: bool
    experiment_isolation: open_webui_isolation_profile
    reset_scopes: dict[str, tuple[Path, ...]]
    managed_environment_overrides: tuple[tuple[str, str], ...] = ()
    managed_config_overrides: tuple[tuple[str, Any], ...] = ()


def load_open_webui_profile(
    repo_root: str | Path,
    llm_baseline_id: str | None = None,
) -> open_webui_profile:
    root = Path(repo_root).resolve()
    llm = load_llm_baseline(root, llm_baseline_id)
    path = root / "infrastructure" / "open-webui" / "profile.yaml"

    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as exc:
        raise profile_error(
            f"Could not read Open WebUI profile '{path}': {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise profile_error(
            f"Could not parse Open WebUI profile '{path}': {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise profile_error("Open WebUI profile must be a YAML mapping.")

    if data.get("schema_version") != "0.1":
        raise profile_error("Open WebUI profile schema_version must be '0.1'.")

    try:
        agent = data["agent"]
        container = data["container"]
        runtime = data["runtime"]
        baseline = data["baseline"]
        isolation_data = data["experiment_isolation"]
        reset_data = data["reset_scopes"]
    except (KeyError, TypeError) as exc:
        raise profile_error(
            f"Open WebUI profile is missing required field: {exc}"
        ) from exc

    web_port = container.get("web_port")
    if not isinstance(web_port, int) or isinstance(web_port, bool):
        raise profile_error("container.web_port must be an integer.")

    reset_scopes: dict[str, tuple[Path, ...]] = {}
    if not isinstance(reset_data, dict):
        raise profile_error("reset_scopes must be a mapping.")

    for scope_name, values in reset_data.items():
        if not isinstance(scope_name, str) or not scope_name:
            raise profile_error("reset scope names must be non-empty strings.")
        if not isinstance(values, list):
            raise profile_error(f"reset_scopes.{scope_name} must be a list.")
        reset_scopes[scope_name] = tuple(
            _safe_relative_path(value, f"reset_scopes.{scope_name}")
            for value in values
        )

    experiment_isolation = _load_open_webui_isolation_profile(isolation_data)

    return open_webui_profile(
        expected_version=_nonempty_string(
            agent.get("expected_version"),
            "agent.expected_version",
        ),
        service_name=_nonempty_string(container.get("service"), "container.service"),
        container_name=_nonempty_string(container.get("name"), "container.name"),
        image=_nonempty_string(container.get("image"), "container.image"),
        web_port=web_port,
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
        llm_requested_context_window=llm.open_webui.requested_context_window,
        ollama_base_url=llm.open_webui.ollama_base_url,
        model_default=llm.model_name,
        webui_auth=_boolean(baseline.get("webui_auth"), "baseline.webui_auth"),
        secret_key_file=_nonempty_string(
            baseline.get("secret_key_file"),
            "baseline.secret_key_file",
        ),
        ollama_api_enabled=_boolean(
            baseline.get("ollama_api_enabled"),
            "baseline.ollama_api_enabled",
        ),
        openai_api_enabled=_boolean(
            baseline.get("openai_api_enabled"),
            "baseline.openai_api_enabled",
        ),
        plugins_enabled=_boolean(
            baseline.get("plugins_enabled"),
            "baseline.plugins_enabled",
        ),
        pip_install_frontmatter_requirements_enabled=_boolean(
            baseline.get("pip_install_frontmatter_requirements_enabled"),
            "baseline.pip_install_frontmatter_requirements_enabled",
        ),
        telemetry_enabled=_boolean(
            baseline.get("telemetry_enabled"),
            "baseline.telemetry_enabled",
        ),
        experiment_isolation=experiment_isolation,
        reset_scopes=reset_scopes,
    )

def _load_open_webui_isolation_profile(
    value: Any,
) -> open_webui_isolation_profile:
    if not isinstance(value, dict):
        raise profile_error("experiment_isolation must be a mapping.")

    fields = (
        "direct_connections_enabled",
        "direct_integrations_enabled",
        "web_search_enabled",
        "code_execution_enabled",
        "code_interpreter_enabled",
        "memories_enabled",
        "memory_system_context_enabled",
        "memory_background_review_enabled",
        "image_generation_enabled",
        "image_prompt_generation_enabled",
        "title_generation_enabled",
        "tags_generation_enabled",
        "follow_up_generation_enabled",
        "autocomplete_generation_enabled",
        "search_query_generation_enabled",
        "retrieval_query_generation_enabled",
        "external_tool_servers_enabled",
        "terminal_servers_enabled",
    )
    parsed = {
        name: _boolean(value.get(name), f"experiment_isolation.{name}")
        for name in fields
    }
    if parsed["external_tool_servers_enabled"]:
        raise profile_error(
            "experiment_isolation.external_tool_servers_enabled must be false."
        )
    if parsed["terminal_servers_enabled"]:
        raise profile_error(
            "experiment_isolation.terminal_servers_enabled must be false."
        )
    return open_webui_isolation_profile(**parsed)
