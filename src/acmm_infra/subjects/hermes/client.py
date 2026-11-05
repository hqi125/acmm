from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex
from typing import Any, Callable, Literal

from acmm_infra.docker import docker_client, require_success
from acmm_infra.process import command_result
from acmm_infra.subjects.hermes.profile import hermes_profile


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

    def profile_operation_count(
        self,
        profile: hermes_profile,
        *,
        unset_config_keys: tuple[str, ...] = (),
    ) -> int:
        settings = _profile_settings(profile)
        extra_operations = 3 if profile.blank_slate.enabled else 0
        return len(unset_config_keys) + len(settings) + extra_operations


    def apply_profile(
        self,
        profile: hermes_profile,
        *,
        timeout_seconds: float,
        output_mode: Literal["quiet", "normal", "verbose"],
        output: Callable[[str], None],
        unset_config_keys: tuple[str, ...] = (),
    ) -> None:
        script = _build_provisioning_script(
            settings=_profile_settings(profile),
            profile=profile,
            unset_config_keys=unset_config_keys,
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
            values=_parse_observation_output(result.stdout, profile),
            error=None,
        )


def _profile_settings(
    profile: hermes_profile,
) -> tuple[tuple[str, str], ...]:
    baseline = (
        ("model.provider", profile.model_provider),
        ("model.base_url", profile.model_base_url),
        ("model.api_mode", profile.model_api_mode),
        ("model.context_length", str(profile.context_window)),
        ("model.default", profile.model_default),
        ("agent.reasoning_effort", profile.reasoning_effort),
        ("terminal.backend", profile.terminal_backend),
        *_experiment_isolation_settings(profile),
    )
    baseline_keys = {key for key, _ in baseline}
    override_keys = [key for key, _ in profile.managed_config_overrides]

    if len(override_keys) != len(set(override_keys)):
        raise ValueError("Hermes managed config overrides must have unique keys.")

    conflicts = sorted(baseline_keys.intersection(override_keys))
    if conflicts:
        raise ValueError(
            "Hermes managed config overrides conflict with baseline-managed keys: "
            + ", ".join(conflicts)
        )

    return baseline + profile.managed_config_overrides


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
    )

    managed_config_commands = tuple(
        (
            f"managed_config_override:{index}",
            [HERMES_CLI, "config", "get", key, "--json"],
        )
        for index, (key, _) in enumerate(profile.managed_config_overrides)
    )

    expected_disabled_command = [
        HERMES_PYTHON,
        "-c",
        _blank_slate_expected_disabled_python(profile),
    ]
    marker_command = [
        "sh",
        "-c",
        _payload_source("bundled_skill_opt_out_marker.sh"),
    ]
    bundled_manifest_command = [
        HERMES_PYTHON,
        "-c",
        _payload_source("bundled_skill_manifest.py"),
    ]
    essential_skills_command = [
        HERMES_PYTHON,
        "-c",
        _payload_source("essential_skills.py"),
    ]
    provider_catalog_command = [
        HERMES_PYTHON,
        "-c",
        _provider_model_catalog_python(profile),
    ]

    commands = (
        commands
        + managed_config_commands
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
    profile: hermes_profile,
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

    observed["managed_config_overrides"] = {
        key: (
            _parse_json_value(raw.get(f"managed_config_override:{index}", ""))
            if raw.get(f"managed_config_override:{index}", "")
            else None
        )
        for index, (key, _) in enumerate(profile.managed_config_overrides)
    }

    return observed


def _provider_model_catalog_python(profile: hermes_profile) -> str:
    base_url = profile.model_base_url.removesuffix("/v1").rstrip("/")
    return (
        _payload_source("provider_model_catalog.py")
        .replace("__BASE_URL__", repr(base_url))
        .replace("__TARGET__", repr(profile.model_default))
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


def _config_unset_operations(
    keys: tuple[str, ...],
) -> list[tuple[str, str]]:
    if len(keys) != len(set(keys)):
        raise ValueError("Hermes managed config cleanup keys must be unique.")

    operations: list[tuple[str, str]] = []
    for key in keys:
        if not key.strip():
            raise ValueError("Hermes managed config cleanup keys must not be empty.")
        command = [HERMES_CLI, "config", "unset", key]
        operations.append(
            (
                f"unset {key}",
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
    profile: hermes_profile,
    *,
    unset_config_keys: tuple[str, ...] = (),
) -> str:
    operations = _config_unset_operations(unset_config_keys)

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

    return _render_provisioning_operations(operations)


def _blank_slate_apply_python(profile: hermes_profile) -> str:
    keep = sorted(profile.blank_slate.minimal_toolsets)
    return _payload_source("blank_slate_apply.py").replace("__KEEP__", repr(keep))


def _blank_slate_expected_disabled_python(
    profile: hermes_profile,
) -> str:
    del profile
    return _payload_source("blank_slate_expected_disabled.py")


def _essential_skill_sync_python() -> str:
    return _payload_source("essential_skill_sync.py")


def _payload_source(name: str) -> str:
    path = Path(__file__).with_name("payloads") / name
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Could not read Hermes payload '{path}': {exc}") from exc


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
