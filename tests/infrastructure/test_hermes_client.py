from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from acmm_infra.hermes_client import (
    _provider_model_catalog_python,
    hermes_client,
)
from acmm_infra.process import command_result
from acmm_infra.profiles.experiments import with_hermes_apparatus
from acmm_infra.profiles.hermes import load_hermes_profile


ROOT = Path(__file__).resolve().parents[2]
APPARATUS_ID = "llm_behavioral_tool_smoke_v1"


class fake_docker:
    def __init__(self) -> None:
        self.provision_result = command_result(
            args=("provision",),
            returncode=0,
            stdout="",
            stderr="",
        )
        self.last_script = ""
        self.last_line_handler = None
        self.last_failure_transform = None
        self.exec_stdout = (
            '__ACMM__model_provider="custom"\n'
            '__ACMM__plugins_enabled=["acmm-tools"]\n'
            '__ACMM__plugin_override:acmm-tools=false\n'
        )

    def provision(
        self,
        script: str,
        timeout_seconds: float,
        *,
        output_mode: str = "normal",
        line_handler=None,
        failure_transform=None,
    ) -> command_result:
        del timeout_seconds, output_mode
        self.last_script = script
        self.last_line_handler = line_handler
        self.last_failure_transform = failure_transform
        result = self.provision_result
        if (
            not result.succeeded
            and failure_transform is not None
        ):
            return failure_transform(result)
        return result

    def exec_script(
        self,
        script: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> command_result:
        del script, timeout_seconds
        return command_result(
            args=("exec-script",),
            returncode=0,
            stdout=self.exec_stdout,
            stderr="",
        )


def test_client_filters_hermes_provision_progress() -> None:
    docker = fake_docker()
    client = hermes_client(docker)  # type: ignore[arg-type]
    profile = load_hermes_profile(ROOT)
    messages: list[str] = []

    client.apply_profile(
        profile,
        timeout_seconds=120.0,
        output_mode="normal",
        output=messages.append,
    )

    assert docker.last_line_handler is not None
    for line in (
        "s6-rc: info: noisy bootstrap",
        "[Hermes 1/14] set model.provider",
        "✓ Set model.provider = custom",
        "permission denied while provisioning",
        "s6-rc: info: noisy shutdown",
    ):
        docker.last_line_handler(line)

    assert messages == [
        "[Hermes 1/14] set model.provider",
        "✓ Set model.provider = custom",
        "permission denied while provisioning",
    ]


def test_client_observes_and_parses_hermes_state() -> None:
    docker = fake_docker()
    client = hermes_client(docker)  # type: ignore[arg-type]
    profile = with_hermes_apparatus(
        ROOT / "tools" / "fixtures" / "llm_behavioral_tool_smoke" / "hermes",
        load_hermes_profile(ROOT),
        APPARATUS_ID,
    )

    observation = client.observe(profile)

    assert observation.succeeded
    assert observation.error is None
    assert observation.values["model_provider"] == "custom"
    assert observation.values["plugins_enabled"] == ["acmm-tools"]
    assert observation.values["plugin_tool_overrides"] == {
        "acmm-tools": False,
    }


def test_provider_model_catalog_probe_is_valid_python() -> None:
    profile = load_hermes_profile(
        ROOT,
        "gpt_oss_120b_cloud_candidate_v1",
    )

    code = _provider_model_catalog_python(profile)

    compile(code, "<hermes-provider-model-probe>", "exec")
    assert "http://host.docker.internal:11434" in code
    assert "api/tags" in code
    assert "gpt-oss:120b-cloud" in code


def test_client_applies_and_observes_managed_config_overrides() -> None:
    docker = fake_docker()
    client = hermes_client(docker)  # type: ignore[arg-type]
    profile = replace(
        load_hermes_profile(ROOT),
        managed_config_overrides=(
            ("external_capabilities.example.url", "https://example.invalid/mcp"),
        ),
    )

    client.apply_profile(
        profile,
        timeout_seconds=120.0,
        output_mode="quiet",
        output=lambda _: None,
    )

    assert "external_capabilities.example.url" in docker.last_script
    assert "https://example.invalid/mcp" in docker.last_script

    docker.exec_stdout = (
        '__ACMM__managed_config_override:0="https://example.invalid/mcp"\n'
    )
    observation = client.observe(profile)

    assert observation.succeeded
    assert observation.values["managed_config_overrides"] == {
        "external_capabilities.example.url": "https://example.invalid/mcp",
    }


def test_client_rejects_managed_config_override_of_baseline_key() -> None:
    docker = fake_docker()
    client = hermes_client(docker)  # type: ignore[arg-type]
    profile = replace(
        load_hermes_profile(ROOT),
        managed_config_overrides=(("model.default", "other-model"),),
    )

    try:
        client.profile_operation_count(profile)
    except ValueError as exc:
        assert "baseline-managed keys" in str(exc)
    else:
        raise AssertionError("Expected baseline-key override to be rejected.")


def test_client_unsets_stale_managed_config_before_profile_apply() -> None:
    docker = fake_docker()
    client = hermes_client(docker)  # type: ignore[arg-type]
    profile = load_hermes_profile(ROOT)

    assert client.profile_operation_count(
        profile,
        unset_config_keys=("external_capabilities.old.url",),
    ) == client.profile_operation_count(profile) + 1

    client.apply_profile(
        profile,
        timeout_seconds=120.0,
        output_mode="quiet",
        output=lambda _: None,
        unset_config_keys=("external_capabilities.old.url",),
    )

    unset = "config unset external_capabilities.old.url"
    first_set = "config set model.provider"
    assert unset in docker.last_script
    assert docker.last_script.index(unset) < docker.last_script.index(first_set)


def test_client_tool_apply_includes_managed_config_and_cleanup() -> None:
    docker = fake_docker()
    client = hermes_client(docker)  # type: ignore[arg-type]
    profile = replace(
        load_hermes_profile(ROOT),
        managed_config_overrides=(
            ("external_capabilities.current.url", "https://example.invalid/mcp"),
        ),
    )

    client.apply_tools(
        profile,
        timeout_seconds=120.0,
        output_mode="quiet",
        output=lambda _: None,
        unset_config_keys=("external_capabilities.old.url",),
    )

    assert "config unset external_capabilities.old.url" in docker.last_script
    assert "config set external_capabilities.current.url" in docker.last_script
