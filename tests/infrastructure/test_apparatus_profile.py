from pathlib import Path
import shutil

import pytest

from acmm_infra.profiles.agent_zero import load_agent_zero_profile
from acmm_infra.profiles.common import profile_error
from acmm_infra.profiles.experiments import (
    apparatus_tree_sha256,
    with_agent_zero_apparatus,
    with_hermes_apparatus,
    with_open_webui_apparatus,
)
from acmm_infra.profiles.hermes import load_hermes_profile
from acmm_infra.profiles.open_webui import load_open_webui_profile


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "tools" / "fixtures" / "llm_behavioral_tool_smoke"
APPARATUS_ID = "llm_behavioral_tool_smoke_v1"


def test_hermes_apparatus_composes_over_baseline() -> None:
    root = FIXTURE_ROOT / "hermes"
    profile = with_hermes_apparatus(root, load_hermes_profile(ROOT), APPARATUS_ID)

    assert len(profile.plugins) == 1
    assert profile.plugins[0].name == "acmm-tools"
    assert profile.plugins[0].enabled is True
    assert profile.plugins[0].toolsets == ("acmm",)
    assert profile.plugins[0].source == Path("plugins/acmm-tools")
    assert profile.plugins[0].source_root == root.resolve()


def test_open_webui_apparatus_composes_over_baseline() -> None:
    root = FIXTURE_ROOT / "open-webui"
    profile = with_open_webui_apparatus(
        root,
        load_open_webui_profile(ROOT),
        APPARATUS_ID,
    )

    assert len(profile.tools) == 1
    assert profile.tools[0].id == "acmm_tools"
    assert profile.tools[0].source == Path("tools/acmm_tools.py")
    assert profile.tools[0].source_root == root.resolve()


def test_agent_zero_apparatus_composes_over_baseline() -> None:
    root = FIXTURE_ROOT / "agent-zero"
    profile = with_agent_zero_apparatus(
        root,
        load_agent_zero_profile(ROOT),
        APPARATUS_ID,
    )

    assert profile.experiment_isolation.allowed_tools == (
        "plugin:acmm_tools:get_magic_number",
        "plugin:acmm_tools:add_numbers",
    )
    assert len(profile.plugins) == 1
    assert profile.plugins[0].name == "acmm_tools"
    assert profile.plugins[0].source == Path("plugins/acmm_tools")
    assert profile.plugins[0].source_root == root.resolve()


def test_apparatus_sha256_ignores_generated_cache_files(tmp_path: Path) -> None:
    root = tmp_path / "apparatus"
    shutil.copytree(FIXTURE_ROOT / "hermes", root)
    expected = apparatus_tree_sha256(root)

    cache_root = root / "plugins" / "acmm-tools" / "__pycache__"
    cache_root.mkdir(exist_ok=True)
    (cache_root / "plugin.cpython-313.pyc").write_bytes(b"generated cache")
    (root / ".pytest_cache" / "state").parent.mkdir()
    (root / ".pytest_cache" / "state").write_text("generated", encoding="utf-8")
    (root / ".DS_Store").write_bytes(b"generated metadata")

    assert apparatus_tree_sha256(root) == expected


def test_hermes_apparatus_rejects_plugin_without_manifest(tmp_path: Path) -> None:
    root = tmp_path / "apparatus"
    plugin_root = root / "plugins" / "acmm-tools"
    plugin_root.mkdir(parents=True)
    (plugin_root / "__init__.py").write_text(
        "def register(ctx):\n    pass\n",
        encoding="utf-8",
    )
    (root / "apparatus.yaml").write_text(
        'schema_version: "0.1"\n'
        'experiment_id: missing_manifest\n'
        'plugins:\n'
        '  managed:\n'
        '    - name: acmm-tools\n'
        '      source: plugins/acmm-tools\n'
        '      destination: plugins/acmm-tools\n'
        '      enabled: true\n'
        '      allow_tool_override: false\n'
        '      toolsets: [acmm]\n',
        encoding="utf-8",
    )

    with pytest.raises(profile_error, match="missing readable Hermes plugin manifest"):
        with_hermes_apparatus(root, load_hermes_profile(ROOT), "missing_manifest")
