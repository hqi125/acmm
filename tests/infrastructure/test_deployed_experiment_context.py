from argparse import Namespace
from pathlib import Path

from acmm_infra.commands.agent_zero import agent_zero_cli
from acmm_infra.commands.hermes import hermes_cli
from acmm_infra.commands.open_webui import open_webui_cli
from acmm_infra.deployment import write_experiment_apparatus_state
from acmm_infra.profiles.agent_zero import load_agent_zero_profile
from acmm_infra.profiles.hermes import load_hermes_profile
from acmm_infra.profiles.open_webui import load_open_webui_profile


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "controlled_tool_use_multiscenario_v1"


def _args(runtime_root: Path) -> Namespace:
    return Namespace(
        repo_root=str(ROOT),
        runtime_root=str(runtime_root),
    )


def _write_marker(
    runtime_root: Path,
    deployment_directory: str,
    *,
    application_scope: str = "experiment",
) -> None:
    write_experiment_apparatus_state(
        runtime_root / deployment_directory,
        experiment_id=EXPERIMENT_ID,
        source_sha256="marker-hash",
        application_scope=application_scope,
        resources=[],
    )


def test_hermes_passive_context_discovers_active_experiment(tmp_path: Path) -> None:
    baseline = load_hermes_profile(ROOT)
    _write_marker(tmp_path, baseline.deployment_directory)

    _, _, profile, _, manager = hermes_cli()._deployed_context(_args(tmp_path))

    assert manager.experiment_id == EXPERIMENT_ID
    assert tuple(plugin.name for plugin in profile.plugins) == ("acmm-tools",)
    assert profile.plugins[0].toolsets == ("acmm",)


def test_open_webui_passive_context_discovers_active_experiment(
    tmp_path: Path,
) -> None:
    baseline = load_open_webui_profile(ROOT)
    _write_marker(tmp_path, baseline.deployment_directory)

    _, _, profile, _, manager = open_webui_cli()._deployed_context(
        _args(tmp_path)
    )

    assert manager.experiment_id == EXPERIMENT_ID
    assert tuple(tool.id for tool in profile.tools) == ("acmm_tools",)


def test_agent_zero_passive_context_discovers_active_experiment(
    tmp_path: Path,
) -> None:
    baseline = load_agent_zero_profile(ROOT)
    _write_marker(tmp_path, baseline.deployment_directory)

    _, _, profile, _, manager = agent_zero_cli()._deployed_context(
        _args(tmp_path)
    )

    assert manager.experiment_id == EXPERIMENT_ID
    assert tuple(plugin.name for plugin in profile.plugins) == ("acmm_tools",)
    assert profile.experiment_isolation.allowed_tools == (
        "plugin:acmm_tools:get_magic_number",
        "plugin:acmm_tools:add_numbers",
    )


def test_passive_context_uses_baseline_when_no_experiment_is_active(
    tmp_path: Path,
) -> None:
    for cli in (hermes_cli(), open_webui_cli(), agent_zero_cli()):
        _, _, _, _, manager = cli._deployed_context(_args(tmp_path))
        assert manager.experiment_id is None


def test_passive_context_discovers_tools_scope_experiment(tmp_path: Path) -> None:
    baseline = load_hermes_profile(ROOT)
    _write_marker(
        tmp_path,
        baseline.deployment_directory,
        application_scope="tools",
    )

    _, _, profile, _, manager = hermes_cli()._deployed_context(_args(tmp_path))

    assert manager.experiment_id == EXPERIMENT_ID
    assert tuple(plugin.name for plugin in profile.plugins) == ("acmm-tools",)
