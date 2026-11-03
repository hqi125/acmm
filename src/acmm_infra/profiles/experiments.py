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


def with_hermes_apparatus(
    apparatus_root: str | Path,
    profile: hermes_profile,
    experiment_id: str,
) -> hermes_profile:
    root = _resolve_apparatus_root(apparatus_root)
    experiment_id = _validate_experiment_id(experiment_id)
    data = _load_apparatus_root(root, "Hermes", experiment_id)
    return _compose_hermes_apparatus(
        profile,
        data,
        experiment_id,
        source_base=root,
        managed_source_root=root,
    )


def _compose_hermes_apparatus(
    profile: hermes_profile,
    data: dict[str, Any],
    experiment_id: str,
    *,
    source_base: Path,
    managed_source_root: Path | None,
) -> hermes_profile:
    plugins_data = _managed_list(data, "plugins", experiment_id, "Hermes")
    plugins: list[managed_hermes_plugin] = []

    for index, plugin in enumerate(plugins_data):
        path = f"plugins.managed[{index}]"
        if not isinstance(plugin, dict):
            raise profile_error(f"{path} must be a mapping.")

        name = _nonempty_string(plugin.get("name"), f"{path}.name")
        source = _safe_relative_path(plugin.get("source"), f"{path}.source")
        _validate_hermes_plugin_package(source_base, source, name, path)

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
                toolsets=_string_tuple(
                    plugin.get("toolsets", []),
                    f"{path}.toolsets",
                ),
                source_root=managed_source_root,
            )
        )

    _require_unique((plugin.name for plugin in plugins), "Hermes plugin name")
    return replace(profile, plugins=tuple(plugins))


def _validate_hermes_plugin_package(
    source_base: str | Path,
    source: Path,
    expected_name: str,
    field_path: str,
) -> None:
    base = Path(source_base).resolve()
    root = (base / source).resolve()
    if not root.is_relative_to(base):
        raise profile_error(f"{field_path}.source escapes its managed source root.")
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


def with_open_webui_apparatus(
    apparatus_root: str | Path,
    profile: open_webui_profile,
    experiment_id: str,
) -> open_webui_profile:
    root = _resolve_apparatus_root(apparatus_root)
    experiment_id = _validate_experiment_id(experiment_id)
    data = _load_apparatus_root(root, "Open WebUI", experiment_id)
    return _compose_open_webui_apparatus(
        profile,
        data,
        experiment_id,
        managed_source_root=root,
    )


def _compose_open_webui_apparatus(
    profile: open_webui_profile,
    data: dict[str, Any],
    experiment_id: str,
    *,
    managed_source_root: Path | None,
) -> open_webui_profile:
    tools_data = _managed_list(data, "tools", experiment_id, "Open WebUI")
    tools: list[managed_open_webui_tool] = []

    for index, tool in enumerate(tools_data):
        path = f"tools.managed[{index}]"
        if not isinstance(tool, dict):
            raise profile_error(f"{path} must be a mapping.")
        tool_id = _nonempty_string(tool.get("id"), f"{path}.id")
        if not tool_id.isidentifier() or tool_id.lower() != tool_id:
            raise profile_error(f"{path}.id must be a lowercase Python identifier.")
        source = _safe_relative_path(tool.get("source"), f"{path}.source")
        if managed_source_root is not None:
            _validate_managed_source(
                managed_source_root,
                source,
                f"{path}.source",
                require_directory=False,
            )
        tools.append(
            managed_open_webui_tool(
                id=tool_id,
                name=_nonempty_string(tool.get("name"), f"{path}.name"),
                description=_nonempty_string(
                    tool.get("description"),
                    f"{path}.description",
                ),
                source=source,
                source_root=managed_source_root,
            )
        )

    _require_unique((tool.id for tool in tools), "Open WebUI tool id")
    return replace(profile, tools=tuple(tools))


def with_agent_zero_apparatus(
    apparatus_root: str | Path,
    profile: agent_zero_profile,
    experiment_id: str,
) -> agent_zero_profile:
    root = _resolve_apparatus_root(apparatus_root)
    experiment_id = _validate_experiment_id(experiment_id)
    data = _load_apparatus_root(root, "Agent Zero", experiment_id)
    return _compose_agent_zero_apparatus(
        profile,
        data,
        experiment_id,
        managed_source_root=root,
    )


def _compose_agent_zero_apparatus(
    profile: agent_zero_profile,
    data: dict[str, Any],
    experiment_id: str,
    *,
    managed_source_root: Path | None,
) -> agent_zero_profile:
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
        source = _safe_relative_path(plugin.get("source"), f"{path}.source")
        if managed_source_root is not None:
            _validate_managed_source(
                managed_source_root,
                source,
                f"{path}.source",
                require_directory=True,
            )
        plugins.append(
            managed_agent_zero_plugin(
                name=_nonempty_string(plugin.get("name"), f"{path}.name"),
                source=source,
                destination=_safe_relative_path(
                    plugin.get("destination"),
                    f"{path}.destination",
                ),
                source_root=managed_source_root,
            )
        )

    _require_unique((plugin.name for plugin in plugins), "Agent Zero plugin name")
    isolation = replace(profile.experiment_isolation, allowed_tools=allowed_tools)
    return replace(profile, experiment_isolation=isolation, plugins=tuple(plugins))


def apparatus_tree_sha256(apparatus_root: str | Path) -> str:
    root = _resolve_apparatus_root(apparatus_root)
    digest = hashlib.sha256()
    for path in iter_managed_source_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _resolve_apparatus_root(apparatus_root: str | Path) -> Path:
    unresolved = Path(apparatus_root).expanduser()
    if unresolved.is_symlink():
        raise profile_error("Experiment apparatus root must not be a symbolic link.")
    root = unresolved.resolve()
    if not root.is_dir():
        raise profile_error(f"Experiment apparatus directory does not exist: '{root}'.")
    return root


def _load_apparatus_root(
    apparatus_root: Path,
    subject_name: str,
    experiment_id: str,
) -> dict[str, Any]:
    path = apparatus_root / "apparatus.yaml"
    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as exc:
        raise profile_error(
            f"Could not read {subject_name} experiment apparatus '{path}': {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise profile_error(
            f"Could not parse {subject_name} experiment apparatus '{path}': {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise profile_error(
            f"{subject_name} experiment apparatus must be a YAML mapping."
        )
    if data.get("schema_version") != "0.1":
        raise profile_error(
            f"{subject_name} experiment apparatus schema_version must be '0.1'."
        )
    if data.get("experiment_id") != experiment_id:
        raise profile_error(
            f"{subject_name} experiment apparatus experiment_id must be "
            f"'{experiment_id}'."
        )
    return data


def _validate_managed_source(
    source_root: Path,
    source: Path,
    field_path: str,
    *,
    require_directory: bool,
) -> None:
    root = source_root.resolve()
    resolved = (root / source).resolve()
    if not resolved.is_relative_to(root):
        raise profile_error(f"{field_path} escapes its managed source root.")

    valid = resolved.is_dir() if require_directory else resolved.is_file()
    if not valid:
        kind = "directory" if require_directory else "file"
        raise profile_error(
            f"{field_path} must identify a managed {kind}: '{resolved}'."
        )


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
