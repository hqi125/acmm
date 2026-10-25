from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import re
from typing import Any

import yaml

from acmm_infra.profiles.agent_zero import (
    agent_zero_profile,
    managed_agent_zero_plugin,
)
from acmm_infra.profiles.common import (
    _boolean,
    _nonempty_string,
    _safe_relative_path,
    _string_tuple,
    profile_error,
)
from acmm_infra.profiles.hermes import hermes_profile, managed_hermes_plugin
from acmm_infra.profiles.open_webui import (
    managed_open_webui_tool,
    open_webui_profile,
)
from acmm_infra.source_tree import iter_managed_source_files


_EXPERIMENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*$")

def with_hermes_experiment(
    repo_root: str | Path,
    profile: hermes_profile,
    experiment_id: str,
) -> hermes_profile:
    data = _load_apparatus(repo_root, "hermes", experiment_id)
    plugins_data = _managed_list(data, "plugins", experiment_id, "Hermes")
    plugins: list[managed_hermes_plugin] = []

    for index, plugin in enumerate(plugins_data):
        path = f"plugins.managed[{index}]"
        if not isinstance(plugin, dict):
            raise profile_error(f"{path} must be a mapping.")

        name = _nonempty_string(plugin.get("name"), f"{path}.name")
        source = _safe_relative_path(plugin.get("source"), f"{path}.source")
        _validate_hermes_plugin_package(repo_root, source, name, path)

        plugins.append(
            managed_hermes_plugin(
                name=name,
                source=source,
                destination=_safe_relative_path(
                    plugin.get("destination"),
                    f"{path}.destination",
                ),
                enabled=bool(plugin.get("enabled", False)),
                allow_tool_override=_boolean(
                    plugin.get("allow_tool_override", False),
                    f"{path}.allow_tool_override",
                ),
                toolsets=_string_tuple(plugin.get("toolsets", []), f"{path}.toolsets"),
            )
        )

    _require_unique((plugin.name for plugin in plugins), "Hermes plugin name")
    return replace(profile, plugins=tuple(plugins))



def _validate_hermes_plugin_package(
    repo_root: str | Path,
    source: Path,
    expected_name: str,
    field_path: str,
) -> None:
    root = (Path(repo_root).resolve() / source).resolve()
    if not root.is_dir():
        raise profile_error(f"{field_path}.source does not exist: '{root}'.")

    entrypoint = root / "__init__.py"
    if not entrypoint.is_file():
        raise profile_error(
            f"{field_path}.source is missing Hermes plugin entrypoint "
            f"'{entrypoint.name}'."
        )

    manifest_path = root / "plugin.yaml"
    try:
        with manifest_path.open("r", encoding="utf-8") as file:
            manifest = yaml.safe_load(file)
    except OSError as exc:
        raise profile_error(
            f"{field_path}.source is missing readable Hermes plugin manifest "
            f"'{manifest_path}': {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise profile_error(
            f"Could not parse Hermes plugin manifest '{manifest_path}': {exc}"
        ) from exc

    if not isinstance(manifest, dict):
        raise profile_error(
            f"Hermes plugin manifest '{manifest_path}' must be a YAML mapping."
        )
    if manifest.get("name") != expected_name:
        raise profile_error(
            f"Hermes plugin manifest '{manifest_path}' name must be "
            f"'{expected_name}'."
        )

def with_open_webui_experiment(
    repo_root: str | Path,
    profile: open_webui_profile,
    experiment_id: str,
) -> open_webui_profile:
    data = _load_apparatus(repo_root, "open-webui", experiment_id)
    tools_data = _managed_list(data, "tools", experiment_id, "Open WebUI")
    tools: list[managed_open_webui_tool] = []

    for index, tool in enumerate(tools_data):
        path = f"tools.managed[{index}]"
        if not isinstance(tool, dict):
            raise profile_error(f"{path} must be a mapping.")
        tool_id = _nonempty_string(tool.get("id"), f"{path}.id")
        if not tool_id.isidentifier() or tool_id.lower() != tool_id:
            raise profile_error(
                f"{path}.id must be a lowercase Python identifier."
            )
        tools.append(
            managed_open_webui_tool(
                id=tool_id,
                name=_nonempty_string(tool.get("name"), f"{path}.name"),
                description=_nonempty_string(
                    tool.get("description"),
                    f"{path}.description",
                ),
                source=_safe_relative_path(tool.get("source"), f"{path}.source"),
            )
        )

    _require_unique((tool.id for tool in tools), "Open WebUI tool id")
    return replace(profile, tools=tuple(tools))


def with_agent_zero_experiment(
    repo_root: str | Path,
    profile: agent_zero_profile,
    experiment_id: str,
) -> agent_zero_profile:
    data = _load_apparatus(repo_root, "agent-zero", experiment_id)
    plugins_data = _managed_list(data, "plugins", experiment_id, "Agent Zero")
    tool_policy = data.get("tool_policy")
    if not isinstance(tool_policy, dict):
        raise profile_error("tool_policy must be a mapping.")

    allowed_tools = _string_tuple(tool_policy.get("allowed", []), "tool_policy.allowed")
    plugins: list[managed_agent_zero_plugin] = []
    for index, plugin in enumerate(plugins_data):
        path = f"plugins.managed[{index}]"
        if not isinstance(plugin, dict):
            raise profile_error(f"{path} must be a mapping.")
        plugins.append(
            managed_agent_zero_plugin(
                name=_nonempty_string(plugin.get("name"), f"{path}.name"),
                source=_safe_relative_path(plugin.get("source"), f"{path}.source"),
                destination=_safe_relative_path(
                    plugin.get("destination"),
                    f"{path}.destination",
                ),
            )
        )

    _require_unique((plugin.name for plugin in plugins), "Agent Zero plugin name")
    isolation = replace(profile.experiment_isolation, allowed_tools=allowed_tools)
    return replace(profile, experiment_isolation=isolation, plugins=tuple(plugins))




def registered_experiment_apparatus_ids(
    repo_root: str | Path,
    subject: str,
) -> tuple[str, ...]:
    root = (
        Path(repo_root).resolve()
        / "infrastructure"
        / subject
        / "experiments"
    )
    if not root.is_dir():
        return ()

    experiment_ids: list[str] = []
    for path in sorted(item for item in root.iterdir() if item.is_dir()):
        experiment_id = _validate_experiment_id(path.name)
        if (path / "apparatus.yaml").is_file():
            experiment_ids.append(experiment_id)
    return tuple(experiment_ids)

def experiment_apparatus_sha256(
    repo_root: str | Path,
    subject: str,
    experiment_id: str,
) -> str:
    experiment_id = _validate_experiment_id(experiment_id)
    root = (
        Path(repo_root).resolve()
        / "infrastructure"
        / subject
        / "experiments"
        / experiment_id
    )
    if not root.is_dir():
        raise profile_error(
            f"Experiment apparatus directory does not exist: '{root}'."
        )

    digest = hashlib.sha256()
    for path in iter_managed_source_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()

def _load_apparatus(
    repo_root: str | Path,
    subject: str,
    experiment_id: str,
) -> dict[str, Any]:
    experiment_id = _validate_experiment_id(experiment_id)
    path = (
        Path(repo_root).resolve()
        / "infrastructure"
        / subject
        / "experiments"
        / experiment_id
        / "apparatus.yaml"
    )
    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as exc:
        raise profile_error(
            f"Could not read {subject} experiment apparatus '{path}': {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise profile_error(
            f"Could not parse {subject} experiment apparatus '{path}': {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise profile_error(f"{subject} experiment apparatus must be a YAML mapping.")
    if data.get("schema_version") != "0.1":
        raise profile_error(
            f"{subject} experiment apparatus schema_version must be '0.1'."
        )
    if data.get("experiment_id") != experiment_id:
        raise profile_error(
            f"{subject} experiment apparatus experiment_id must be '{experiment_id}'."
        )
    return data


def _validate_experiment_id(value: str) -> str:
    experiment_id = _nonempty_string(value, "experiment_id")
    if _EXPERIMENT_ID_PATTERN.fullmatch(experiment_id) is None:
        raise profile_error(
            "experiment_id must contain only lowercase letters, digits, and underscores."
        )
    return experiment_id


def _managed_list(
    data: dict[str, Any],
    section_name: str,
    experiment_id: str,
    subject_name: str,
) -> list[Any]:
    section = data.get(section_name)
    if not isinstance(section, dict):
        raise profile_error(
            f"{subject_name} apparatus for '{experiment_id}' requires "
            f"a {section_name} mapping."
        )
    managed = section.get("managed")
    if not isinstance(managed, list):
        raise profile_error(f"{section_name}.managed must be a list.")
    return managed


def _require_unique(values, description: str) -> None:
    values = tuple(values)
    if len(values) != len(set(values)):
        raise profile_error(f"Duplicate {description} in experiment apparatus.")
