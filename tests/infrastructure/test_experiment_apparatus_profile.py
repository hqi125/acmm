from pathlib import Path

import pytest

from acmm_infra.profiles.agent_zero import load_agent_zero_profile
from acmm_infra.profiles.common import profile_error
from acmm_infra.profiles.experiments import (
    experiment_apparatus_sha256,
    with_agent_zero_experiment,
    with_hermes_experiment,
    with_open_webui_experiment,
)
from acmm_infra.profiles.hermes import load_hermes_profile
from acmm_infra.profiles.open_webui import load_open_webui_profile


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "controlled_tool_use_multiscenario_v1"


def test_hermes_experiment_apparatus_composes_over_baseline() -> None:
    profile = with_hermes_experiment(
        ROOT,
        load_hermes_profile(ROOT),
        EXPERIMENT_ID,
    )

    assert len(profile.plugins) == 1
    assert profile.plugins[0].name == "acmm-tools"
    assert profile.plugins[0].enabled is True
    assert profile.plugins[0].toolsets == ("acmm",)
    assert profile.plugins[0].source == Path(
        "infrastructure/hermes/experiments/"
        + EXPERIMENT_ID
        + "/plugins/acmm-tools"
    )
    assert profile.plugins[0].source_root is None


def test_open_webui_experiment_apparatus_composes_over_baseline() -> None:
    profile = with_open_webui_experiment(
        ROOT,
        load_open_webui_profile(ROOT),
        EXPERIMENT_ID,
    )

    assert len(profile.tools) == 1
    assert profile.tools[0].id == "acmm_tools"
    assert profile.tools[0].source == Path(
        f"experiments/{EXPERIMENT_ID}/tools/acmm_tools.py"
    )
    assert profile.tools[0].source_root is None


def test_agent_zero_experiment_apparatus_composes_over_baseline() -> None:
    profile = with_agent_zero_experiment(
        ROOT,
        load_agent_zero_profile(ROOT),
        EXPERIMENT_ID,
    )

    assert profile.experiment_isolation.allowed_tools == (
        "plugin:acmm_tools:get_magic_number",
        "plugin:acmm_tools:add_numbers",
    )
    assert len(profile.plugins) == 1
    assert profile.plugins[0].name == "acmm_tools"
    assert profile.plugins[0].source == Path(
        f"experiments/{EXPERIMENT_ID}/plugins/acmm_tools"
    )
    assert profile.plugins[0].source_root is None


def test_existing_experiments_have_independent_apparatus_copies() -> None:
    pilot = "magic_number_tool_use_v1"
    canonical = "controlled_tool_use_multiscenario_v1"

    for subject in ("hermes", "open-webui", "agent-zero"):
        pilot_root = ROOT / "infrastructure" / subject / "experiments" / pilot
        canonical_root = ROOT / "infrastructure" / subject / "experiments" / canonical
        assert pilot_root.is_dir()
        assert canonical_root.is_dir()
        assert experiment_apparatus_sha256(ROOT, subject, pilot) != (
            experiment_apparatus_sha256(ROOT, subject, canonical)
        )


def test_apparatus_sha256_ignores_generated_cache_files(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    apparatus_root = (
        repo_root
        / "infrastructure"
        / "hermes"
        / "experiments"
        / EXPERIMENT_ID
    )
    plugin_root = apparatus_root / "plugins" / "acmm-tools"
    plugin_root.mkdir(parents=True)
    (apparatus_root / "apparatus.yaml").write_text(
        "schema_version: \"0.1\"\n",
        encoding="utf-8",
    )
    (plugin_root / "__init__.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    expected = experiment_apparatus_sha256(
        repo_root,
        "hermes",
        EXPERIMENT_ID,
    )

    cache_root = plugin_root / "__pycache__"
    cache_root.mkdir()
    (cache_root / "__init__.cpython-313.pyc").write_bytes(b"generated cache")
    (plugin_root / ".pytest_cache" / "state").parent.mkdir()
    (plugin_root / ".pytest_cache" / "state").write_text(
        "generated",
        encoding="utf-8",
    )
    (plugin_root / ".DS_Store").write_bytes(b"generated metadata")

    assert experiment_apparatus_sha256(
        repo_root,
        "hermes",
        EXPERIMENT_ID,
    ) == expected

def test_hermes_experiment_rejects_plugin_without_manifest(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    baseline_root = ROOT / "infrastructure" / "hermes"
    target_root = repo_root / "infrastructure" / "hermes"
    target_root.mkdir(parents=True)

    (target_root / "profile.yaml").write_bytes(
        (baseline_root / "profile.yaml").read_bytes()
    )
    llm_source = ROOT / "infrastructure" / "llm-baselines"
    llm_target = repo_root / "infrastructure" / "llm-baselines"
    llm_target.mkdir(parents=True)
    for source in llm_source.iterdir():
        if source.is_file():
            (llm_target / source.name).write_bytes(source.read_bytes())
    apparatus_root = target_root / "experiments" / "missing_manifest"
    plugin_root = apparatus_root / "plugins" / "acmm-tools"
    plugin_root.mkdir(parents=True)
    (plugin_root / "__init__.py").write_text("def register(ctx):\n    pass\n")
    (apparatus_root / "apparatus.yaml").write_text(
        'schema_version: "0.1"\n'
        'experiment_id: missing_manifest\n'
        'plugins:\n'
        '  managed:\n'
        '    - name: acmm-tools\n'
        '      source: infrastructure/hermes/experiments/missing_manifest/'
        'plugins/acmm-tools\n'
        '      destination: plugins/acmm-tools\n'
        '      enabled: true\n'
        '      allow_tool_override: false\n'
        '      toolsets: [acmm]\n',
        encoding="utf-8",
    )

    profile = load_hermes_profile(repo_root)
    with pytest.raises(profile_error, match="missing readable Hermes plugin manifest"):
        with_hermes_experiment(repo_root, profile, "missing_manifest")

