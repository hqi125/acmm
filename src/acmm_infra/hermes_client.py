from __future__ import annotations

from dataclasses import dataclass
import json
import shlex
from typing import Any, Callable, Literal

from acmm_infra.docker import docker_client, require_success
from acmm_infra.process import command_result
from acmm_infra.profiles.hermes import hermes_profile, managed_hermes_plugin


HERMES_CLI = "/opt/hermes/bin/hermes"
HERMES_PYTHON = "/opt/hermes/.venv/bin/python"


@dataclass(frozen=True)
class hermes_observation:
    succeeded: bool
    values: dict[str, Any]
    error: str | None


class hermes_client:
    def __init__(self, docker: docker_client) -> None:
        self.docker = docker

    def profile_operation_count(self, profile: hermes_profile) -> int:
        settings = _profile_settings(profile)
        extra_operations = 3 if profile.blank_slate.enabled else 0
        residue_operation = 1 if _disabled_plugin_toolsets(profile.plugins) else 0
        return (
            len(settings)
            + len(profile.plugins)
            + extra_operations
            + residue_operation
        )

    def tool_operation_count(self, profile: hermes_profile) -> int:
        toolset_operation = 1 if profile.plugins else 0
        residue_operation = 1 if _disabled_plugin_toolsets(profile.plugins) else 0
        return 1 + toolset_operation + len(profile.plugins) + residue_operation

    def apply_profile(
        self,
        profile: hermes_profile,
        *,
        timeout_seconds: float,
        output_mode: Literal["quiet", "normal", "verbose"],
        output: Callable[[str], None],
    ) -> None:
        script = _build_provisioning_script(
            settings=_profile_settings(profile),
            plugins=profile.plugins,
            profile=profile,
        )
        result = self.docker.provision(
            script,
            timeout_seconds=timeout_seconds,
            output_mode=output_mode,
            line_handler=(
                lambda line: _emit_provision_progress(line, output)
            ),
            failure_transform=_with_concise_provision_error,
        )
        require_success(result, "Hermes batched provisioning")

    def apply_tools(
        self,
        profile: hermes_profile,
        *,
        timeout_seconds: float,
        output_mode: Literal["quiet", "normal", "verbose"],
        output: Callable[[str], None],
    ) -> None:
        settings = (
            (
                "tools.tool_search.enabled",
                profile.experiment_isolation.tool_search_enabled,
            ),
        )
        script = _build_tool_provisioning_script(
            settings=settings,
            plugins=profile.plugins,
        )
        result = self.docker.provision(
            script,
            timeout_seconds=timeout_seconds,
            output_mode=output_mode,
            line_handler=(
                lambda line: _emit_provision_progress(line, output)
            ),
            failure_transform=_with_concise_provision_error,
        )
        require_success(result, "Hermes managed-tool provisioning")

    def observe(
        self,
        profile: hermes_profile,
        *,
        timeout_seconds: float = 30.0,
    ) -> hermes_observation:
        result = self.docker.exec_script(
            _build_observation_script(profile),
            timeout_seconds=timeout_seconds,
        )
        if not result.succeeded:
            return hermes_observation(
                succeeded=False,
                values={},
                error=_result_detail(result),
            )
        return hermes_observation(
            succeeded=True,
            values=_parse_observation_output(result.stdout, profile.plugins),
            error=None,
        )


def _profile_settings(
    profile: hermes_profile,
) -> tuple[tuple[str, str], ...]:
    return (
        ("model.provider", profile.model_provider),
        ("model.base_url", profile.model_base_url),
        ("model.api_mode", profile.model_api_mode),
        ("model.context_length", str(profile.context_window)),
        ("model.default", profile.model_default),
        ("agent.reasoning_effort", profile.reasoning_effort),
        ("terminal.backend", profile.terminal_backend),
        *_experiment_isolation_settings(profile),
    )


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
        (
            "title_generation_enabled",
            [
                HERMES_CLI,
                "config",
                "get",
                "auxiliary.title_generation.enabled",
                "--json",
            ],
        ),
        (
            "background_review_enabled",
            [
                HERMES_CLI,
                "config",
                "get",
                "auxiliary.background_review.enabled",
                "--json",
            ],
        ),
        (
            "tool_search_enabled",
            [
                HERMES_CLI,
                "config",
                "get",
                "tools.tool_search.enabled",
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
    provider_catalog_command = [
        HERMES_PYTHON,
        "-c",
        _provider_model_catalog_python(profile),
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
            (
                "provider_model_catalog",
                provider_catalog_command,
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
    managed_hermes_plugins: tuple[managed_hermes_plugin, ...],
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
        "title_generation_enabled",
        "background_review_enabled",
        "tool_search_enabled",
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

    provider_catalog = _parse_json_value(raw.get("provider_model_catalog", ""))
    if isinstance(provider_catalog, dict):
        observed["provider_model_available"] = provider_catalog.get("available")
        model_ids = provider_catalog.get("models")
        observed["provider_model_ids"] = (
            [item for item in model_ids if isinstance(item, str)]
            if isinstance(model_ids, list)
            else []
        )
        error = provider_catalog.get("error")
        observed["provider_model_error"] = error if isinstance(error, str) else None
    else:
        observed["provider_model_available"] = None
        observed["provider_model_ids"] = []
        observed["provider_model_error"] = "provider model catalog could not be read"

    plugins = _parse_json_value(raw.get("plugins_enabled", ""))
    observed["plugins_enabled"] = (
        [item for item in plugins if isinstance(item, str)]
        if isinstance(plugins, list)
        else []
    )

    observed["plugin_tool_overrides"] = {}

    for plugin in managed_hermes_plugins:
        raw_value = raw.get(f"plugin_override:{plugin.name}", "")
        value = _parse_json_scalar(raw_value) if raw_value else None
        observed["plugin_tool_overrides"][plugin.name] = (
            value if isinstance(value, bool) else None
        )

    return observed


def _provider_model_catalog_python(profile: hermes_profile) -> str:
    base_url = profile.model_base_url.removesuffix("/v1").rstrip("/")
    return (
        "import json,urllib.request; "
        f"url={base_url!r} + '/api/tags'; "
        f"target={profile.model_default!r}; "
        "out={'available':False,'models':[],'error':None}; "
        "\ntry:\n"
        " data=json.load(urllib.request.urlopen(url, timeout=10)); "
        " models=[str(item.get('name') or item.get('model')) for item in "
        "data.get('models',[]) if isinstance(item,dict) and "
        "(item.get('name') or item.get('model'))]; "
        " out={'available':target in models,'models':models,'error':None} "
        "\nexcept Exception as exc:\n"
        " out={'available':False,'models':[],'error':str(exc)} "
        "\nprint(json.dumps(out,separators=(',',':')))"
    )


def _experiment_isolation_settings(
    profile: hermes_profile,
) -> tuple[tuple[str, str], ...]:
    isolation = profile.experiment_isolation

    return (
        (
            "auxiliary.title_generation.enabled",
            "true" if isolation.title_generation_enabled else "false",
        ),
        (
            "auxiliary.background_review.enabled",
            "true" if isolation.background_review_enabled else "false",
        ),
        (
            "tools.tool_search.enabled",
            isolation.tool_search_enabled,
        ),
    )


def _build_tool_provisioning_script(
    settings: tuple[tuple[str, str], ...],
    plugins: tuple[managed_hermes_plugin, ...],
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

    if plugins:
        command = [
            HERMES_PYTHON,
            "-c",
            _experiment_toolset_policy_python(plugins),
        ]
        operations.append(
            (
                "reconcile experiment toolset visibility",
                " ".join(shlex.quote(part) for part in command),
            )
        )

    operations.extend(_plugin_operations(plugins))

    disabled_toolsets = _disabled_plugin_toolsets(plugins)
    if disabled_toolsets:
        command = [
            HERMES_PYTHON,
            "-c",
            _purge_disabled_plugin_toolsets_python(disabled_toolsets),
        ]
        operations.append(
            (
                "purge disabled plugin toolset residue",
                " ".join(shlex.quote(part) for part in command),
            )
        )

    return _render_provisioning_operations(operations)


def _experiment_toolset_policy_python(
    plugins: tuple[managed_hermes_plugin, ...],
) -> str:
    enabled = sorted(
        {
            toolset
            for plugin in plugins
            if plugin.enabled
            for toolset in plugin.toolsets
        }
    )
    disabled = sorted(
        {
            toolset
            for plugin in plugins
            if not plugin.enabled
            for toolset in plugin.toolsets
        }
        - set(enabled)
    )

    return f"""
from hermes_cli.setup import load_config, save_config

enabled = set({enabled!r})
disabled_overlay = set({disabled!r})
config = load_config()
agent = config.setdefault("agent", {{}})
disabled_toolsets = set(agent.get("disabled_toolsets") or [])
disabled_toolsets.update(disabled_overlay)
disabled_toolsets.difference_update(enabled)
agent["disabled_toolsets"] = sorted(disabled_toolsets)
save_config(config)
print(
    "✓ Experiment toolset visibility reconciled: enabled="
    + (",".join(sorted(enabled)) or "none")
    + "; disabled="
    + (",".join(sorted(disabled_overlay)) or "none")
)
""".strip()


def _disabled_plugin_toolsets(
    plugins: tuple[managed_hermes_plugin, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                toolset
                for plugin in plugins
                if not plugin.enabled
                for toolset in plugin.toolsets
            }
        )
    )


def _purge_disabled_plugin_toolsets_python(
    toolsets: tuple[str, ...],
) -> str:
    return f"""
from hermes_cli.setup import load_config, save_config

purge = set({list(toolsets)!r})
config = load_config()
agent = config.setdefault("agent", {{}})
disabled = set(agent.get("disabled_toolsets") or [])
disabled.difference_update(purge)
agent["disabled_toolsets"] = sorted(disabled)
save_config(config)
print(
    "✓ Disabled plugin toolset residue purged: "
    + ",".join(sorted(purge))
)
""".strip()


def _plugin_operations(
    plugins: tuple[managed_hermes_plugin, ...],
) -> list[tuple[str, str]]:
    operations: list[tuple[str, str]] = []

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

    return operations


def _render_provisioning_operations(
    operations: list[tuple[str, str]],
) -> str:
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


def _build_provisioning_script(
    settings: tuple[tuple[str, str], ...],
    plugins: tuple[managed_hermes_plugin, ...],
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

    operations.extend(_plugin_operations(plugins))

    disabled_toolsets = _disabled_plugin_toolsets(plugins)
    if disabled_toolsets:
        command = [
            HERMES_PYTHON,
            "-c",
            _purge_disabled_plugin_toolsets_python(disabled_toolsets),
        ]
        operations.append(
            (
                "purge disabled plugin toolset residue",
                " ".join(shlex.quote(part) for part in command),
            )
        )

    return _render_provisioning_operations(operations)


def _blank_slate_apply_python(
    profile: hermes_profile,
) -> str:
    keep = sorted(profile.blank_slate.minimal_toolsets)
    overlay = sorted(managed_overlay_toolsets(profile.plugins))

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
    overlay = sorted(managed_overlay_toolsets(profile.plugins))

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


def managed_overlay_toolsets(
    plugins: tuple[managed_hermes_plugin, ...],
) -> tuple[str, ...]:
    toolsets = {
        toolset
        for plugin in plugins
        if plugin.enabled
        for toolset in plugin.toolsets
    }
    return tuple(sorted(toolsets))


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


def _emit_provision_progress(
    line: str,
    output: Callable[[str], None],
) -> None:
    stripped = line.strip()
    lower = stripped.lower()
    if (
        stripped.startswith("[Hermes")
        or stripped.startswith("✓")
        or "traceback" in lower
        or "importerror" in lower
        or "modulenotfounderror" in lower
        or "exception:" in lower
        or "cannot import" in lower
        or "not found" in lower
        or "permission denied" in lower
        or "no such file" in lower
    ):
        output(stripped)


def _with_concise_provision_error(
    result: command_result,
) -> command_result:
    interesting: list[str] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if (
            stripped.startswith("[Hermes")
            or "error" in lower
            or "failed" in lower
            or "timed out" in lower
            or "not found" in lower
            or "invalid" in lower
            or "permission denied" in lower
            or "refus" in lower
            or "traceback" in lower
            or "usage:" in lower
        ):
            interesting.append(stripped)
    if not interesting:
        interesting = [
            line.strip()
            for line in result.stdout.splitlines()[-12:]
            if line.strip()
        ]
    detail_lines = interesting[-20:]
    if result.stderr.strip():
        detail_lines.append(result.stderr.strip())
    detail = "\n".join(detail_lines)
    return command_result(
        args=result.args,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=detail,
        timed_out=result.timed_out,
    )


def _result_detail(result: command_result) -> str:
    return (
        result.stdout.strip()
        or result.stderr.strip()
        or f"exit status {result.returncode}"
    )
