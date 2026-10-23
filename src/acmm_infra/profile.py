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


@dataclass(frozen=True)
class managed_open_webui_tool:
    id: str
    name: str
    description: str
    source: Path


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
    tools: tuple[managed_open_webui_tool, ...]
    reset_scopes: dict[str, tuple[Path, ...]]


def load_open_webui_profile(
    repo_root: str | Path,
) -> open_webui_profile:
    root = Path(repo_root).resolve()
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
        tools_data = data["tools"]["managed"]
        reset_data = data["reset_scopes"]
    except (KeyError, TypeError) as exc:
        raise profile_error(
            f"Open WebUI profile is missing required field: {exc}"
        ) from exc

    web_port = container.get("web_port")
    if not isinstance(web_port, int) or isinstance(web_port, bool):
        raise profile_error("container.web_port must be an integer.")

    if not isinstance(tools_data, list):
        raise profile_error("tools.managed must be a list.")

    tools: list[managed_open_webui_tool] = []
    tool_ids: set[str] = set()
    for index, tool in enumerate(tools_data):
        if not isinstance(tool, dict):
            raise profile_error(f"tools.managed[{index}] must be a mapping.")

        tool_id = _nonempty_string(tool.get("id"), f"tools.managed[{index}].id")
        if not tool_id.isidentifier() or tool_id.lower() != tool_id:
            raise profile_error(
                f"tools.managed[{index}].id must be a lowercase Python identifier."
            )
        if tool_id in tool_ids:
            raise profile_error(f"Duplicate Open WebUI tool id: '{tool_id}'.")
        tool_ids.add(tool_id)

        tools.append(
            managed_open_webui_tool(
                id=tool_id,
                name=_nonempty_string(
                    tool.get("name"),
                    f"tools.managed[{index}].name",
                ),
                description=_nonempty_string(
                    tool.get("description"),
                    f"tools.managed[{index}].description",
                ),
                source=_safe_relative_path(
                    tool.get("source"),
                    f"tools.managed[{index}].source",
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
        ollama_base_url=_nonempty_string(
            baseline.get("ollama_base_url"),
            "baseline.ollama_base_url",
        ),
        model_default=_nonempty_string(
            baseline.get("model_default"),
            "baseline.model_default",
        ),
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
        tools=tuple(tools),
        reset_scopes=reset_scopes,
    )



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
) -> agent_zero_profile:
    root = Path(repo_root).resolve()
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

    context_window = baseline.get("context_window")
    utility_context_window = baseline.get("utility_context_window")
    for field_name, value in (
        ("baseline.context_window", context_window),
        ("baseline.utility_context_window", utility_context_window),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise profile_error(f"{field_name} must be a positive integer.")

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
        agent_profile=_nonempty_string(
            baseline.get("agent_profile"),
            "baseline.agent_profile",
        ),
        model_preset=_nonempty_string(
            baseline.get("model_preset"),
            "baseline.model_preset",
        ),
        model_provider=_nonempty_string(
            baseline.get("model_provider"),
            "baseline.model_provider",
        ),
        model_base_url=_nonempty_string(
            baseline.get("model_base_url"),
            "baseline.model_base_url",
        ),
        model_default=_nonempty_string(
            baseline.get("model_default"),
            "baseline.model_default",
        ),
        context_window=context_window,
        utility_model_provider=_nonempty_string(
            baseline.get("utility_model_provider"),
            "baseline.utility_model_provider",
        ),
        utility_model_base_url=_nonempty_string(
            baseline.get("utility_model_base_url"),
            "baseline.utility_model_base_url",
        ),
        utility_model_default=_nonempty_string(
            baseline.get("utility_model_default"),
            "baseline.utility_model_default",
        ),
        utility_context_window=utility_context_window,
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
