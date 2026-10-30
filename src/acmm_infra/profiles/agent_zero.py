from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
class managed_agent_zero_plugin:
    name: str
    source: Path
    destination: Path


@dataclass(frozen=True)
class agent_zero_isolation_profile:
    mcp_server_enabled: bool
    a2a_server_enabled: bool
    update_check_enabled: bool
    chat_inherit_project: bool
    rfc_auto_docker: bool
    tool_default: str
    mcp_tool_default: str
    allowed_tools: tuple[str, ...]
    skills_default: str
    memory_recall_enabled: bool
    memory_memorize_enabled: bool
    memory_memorize_consolidation: bool
    automatic_chat_naming: bool


@dataclass(frozen=True)
class agent_zero_profile:
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
    agent_profile: str
    model_preset: str
    model_provider: str
    model_base_url: str
    model_default: str
    context_window: int
    utility_model_provider: str
    utility_model_base_url: str
    utility_model_default: str
    utility_context_window: int
    embedding_model_provider: str
    embedding_model_default: str
    experiment_isolation: agent_zero_isolation_profile
    plugins: tuple[managed_agent_zero_plugin, ...]
    reset_scopes: dict[str, tuple[Path, ...]]


def load_agent_zero_profile(
    repo_root: str | Path,
    llm_baseline_id: str | None = None,
) -> agent_zero_profile:
    root = Path(repo_root).resolve()
    llm = load_llm_baseline(root, llm_baseline_id)
    path = root / "infrastructure" / "agent-zero" / "profile.yaml"

    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as exc:
        raise profile_error(
            f"Could not read Agent Zero profile '{path}': {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise profile_error(
            f"Could not parse Agent Zero profile '{path}': {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise profile_error("Agent Zero profile must be a YAML mapping.")
    if data.get("schema_version") != "0.1":
        raise profile_error("Agent Zero profile schema_version must be '0.1'.")

    try:
        agent = data["agent"]
        container = data["container"]
        runtime = data["runtime"]
        baseline = data["baseline"]
        isolation_data = data["experiment_isolation"]
        plugins_data = data["plugins"]["managed"]
        reset_data = data["reset_scopes"]
    except (KeyError, TypeError) as exc:
        raise profile_error(
            f"Agent Zero profile is missing required field: {exc}"
        ) from exc

    web_port = container.get("web_port")
    if not isinstance(web_port, int) or isinstance(web_port, bool):
        raise profile_error("container.web_port must be an integer.")

    if not isinstance(isolation_data, dict):
        raise profile_error("experiment_isolation must be a mapping.")
    tool_policy = isolation_data.get("tool_policy")
    skills_policy = isolation_data.get("skills_policy")
    memory = isolation_data.get("memory")
    chat_naming = isolation_data.get("chat_naming")
    if not isinstance(tool_policy, dict):
        raise profile_error("experiment_isolation.tool_policy must be a mapping.")
    if not isinstance(skills_policy, dict):
        raise profile_error("experiment_isolation.skills_policy must be a mapping.")
    if not isinstance(memory, dict):
        raise profile_error("experiment_isolation.memory must be a mapping.")
    if not isinstance(chat_naming, dict):
        raise profile_error("experiment_isolation.chat_naming must be a mapping.")

    tool_default = _nonempty_string(
        tool_policy.get("default"),
        "experiment_isolation.tool_policy.default",
    )
    mcp_tool_default = _nonempty_string(
        tool_policy.get("mcp_default"),
        "experiment_isolation.tool_policy.mcp_default",
    )
    skills_default = _nonempty_string(
        skills_policy.get("default"),
        "experiment_isolation.skills_policy.default",
    )
    for field_name, value in (
        ("experiment_isolation.tool_policy.default", tool_default),
        ("experiment_isolation.tool_policy.mcp_default", mcp_tool_default),
        ("experiment_isolation.skills_policy.default", skills_default),
    ):
        if value not in {"allow", "block"}:
            raise profile_error(f"{field_name} must be 'allow' or 'block'.")

    isolation = agent_zero_isolation_profile(
        mcp_server_enabled=_boolean(
            isolation_data.get("mcp_server_enabled"),
            "experiment_isolation.mcp_server_enabled",
        ),
        a2a_server_enabled=_boolean(
            isolation_data.get("a2a_server_enabled"),
            "experiment_isolation.a2a_server_enabled",
        ),
        update_check_enabled=_boolean(
            isolation_data.get("update_check_enabled"),
            "experiment_isolation.update_check_enabled",
        ),
        chat_inherit_project=_boolean(
            isolation_data.get("chat_inherit_project"),
            "experiment_isolation.chat_inherit_project",
        ),
        rfc_auto_docker=_boolean(
            isolation_data.get("rfc_auto_docker"),
            "experiment_isolation.rfc_auto_docker",
        ),
        tool_default=tool_default,
        mcp_tool_default=mcp_tool_default,
        allowed_tools=_string_tuple(
            tool_policy.get("allowed", []),
            "experiment_isolation.tool_policy.allowed",
        ),
        skills_default=skills_default,
        memory_recall_enabled=_boolean(
            memory.get("recall_enabled"),
            "experiment_isolation.memory.recall_enabled",
        ),
        memory_memorize_enabled=_boolean(
            memory.get("memorize_enabled"),
            "experiment_isolation.memory.memorize_enabled",
        ),
        memory_memorize_consolidation=_boolean(
            memory.get("memorize_consolidation"),
            "experiment_isolation.memory.memorize_consolidation",
        ),
        automatic_chat_naming=_boolean(
            chat_naming.get("automatic_naming"),
            "experiment_isolation.chat_naming.automatic_naming",
        ),
    )

    if not isinstance(plugins_data, list):
        raise profile_error("plugins.managed must be a list.")

    plugins: list[managed_agent_zero_plugin] = []
    plugin_names: set[str] = set()
    for index, plugin in enumerate(plugins_data):
        if not isinstance(plugin, dict):
            raise profile_error(f"plugins.managed[{index}] must be a mapping.")

        name = _nonempty_string(
            plugin.get("name"),
            f"plugins.managed[{index}].name",
        )
        if name in plugin_names:
            raise profile_error(f"Duplicate Agent Zero plugin name: '{name}'.")
        plugin_names.add(name)
        plugins.append(
            managed_agent_zero_plugin(
                name=name,
                source=_safe_relative_path(
                    plugin.get("source"),
                    f"plugins.managed[{index}].source",
                ),
                destination=_safe_relative_path(
                    plugin.get("destination"),
                    f"plugins.managed[{index}].destination",
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
            _safe_relative_path(value, f"reset_scopes.{scope_name}")
            for value in values
        )

    return agent_zero_profile(
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
        agent_profile=_nonempty_string(
            baseline.get("agent_profile"),
            "baseline.agent_profile",
        ),
        model_preset=_nonempty_string(
            baseline.get("model_preset"),
            "baseline.model_preset",
        ),
        model_provider=llm.agent_zero.model_provider,
        model_base_url=llm.agent_zero.model_base_url,
        model_default=llm.model_name,
        context_window=llm.agent_zero.requested_context_window,
        utility_model_provider=llm.agent_zero.utility_model_provider,
        utility_model_base_url=llm.agent_zero.utility_model_base_url,
        utility_model_default=llm.agent_zero.utility_model_name,
        utility_context_window=llm.agent_zero.utility_requested_context_window,
        embedding_model_provider=_nonempty_string(
            baseline.get("embedding_model_provider"),
            "baseline.embedding_model_provider",
        ),
        embedding_model_default=_nonempty_string(
            baseline.get("embedding_model_default"),
            "baseline.embedding_model_default",
        ),
        experiment_isolation=isolation,
        plugins=tuple(plugins),
        reset_scopes=reset_scopes,
    )
