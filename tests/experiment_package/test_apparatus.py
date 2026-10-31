from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from acmm.experiment_package import (
    acquire_experiment_package,
    apparatus_provenance,
    experiment_apparatus_error,
    load_experiment_package,
    load_staged_experiment_apparatus,
)
from acmm_infra.agent_zero import agent_zero_manager
from acmm_infra.cohort import cohort_subject, inspect_subject_context
from acmm_infra.deployment import write_experiment_apparatus_state
from acmm_infra.hermes import hermes_manager
from acmm_infra.open_webui import open_webui_manager
from acmm_infra.subject_managers import (
    build_deployed_subject_manager,
    build_package_subject_manager,
)
from acmm_infra.profiles.agent_zero import load_agent_zero_profile
from acmm_infra.profiles.experiments import (
    with_agent_zero_apparatus,
    with_hermes_apparatus,
    with_open_webui_apparatus,
)
from acmm_infra.profiles.hermes import load_hermes_profile
from acmm_infra.profiles.open_webui import load_open_webui_profile


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = ROOT / "contracts" / "v0_1" / "contract.schema.json"
VALID_CONTRACT = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "magic_number.yaml"
EXPERIMENT_ID = "magic_number_tool_use_v1"


def _write_apparatus_package(root: Path) -> None:
    root.mkdir(parents=True)
    shutil.copy2(VALID_CONTRACT, root / "experiment.yaml")
    (root / "experiment-package.yaml").write_text(
        'schema_version: "0.1"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "contract: experiment.yaml\n"
        "apparatus:\n"
        "  hermes: apparatus/hermes\n"
        "  open-webui: apparatus/open-webui\n"
        "  agent-zero: apparatus/agent-zero\n",
        encoding="utf-8",
    )

    hermes_root = root / "apparatus" / "hermes"
    hermes_plugin = hermes_root / "plugins" / "acmm-tools"
    hermes_plugin.mkdir(parents=True)
    (hermes_root / "apparatus.yaml").write_text(
        'schema_version: "0.1"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "plugins:\n"
        "  managed:\n"
        "    - name: acmm-tools\n"
        "      source: plugins/acmm-tools\n"
        "      destination: plugins/acmm-tools\n"
        "      enabled: true\n"
        "      allow_tool_override: false\n"
        "      toolsets: [acmm]\n",
        encoding="utf-8",
    )
    (hermes_plugin / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (hermes_plugin / "plugin.yaml").write_text(
        "name: acmm-tools\nversion: \"0.1\"\n",
        encoding="utf-8",
    )

    open_webui_root = root / "apparatus" / "open-webui"
    open_webui_tool = open_webui_root / "tools" / "acmm_tools.py"
    open_webui_tool.parent.mkdir(parents=True)
    (open_webui_root / "apparatus.yaml").write_text(
        'schema_version: "0.1"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "tools:\n"
        "  managed:\n"
        "    - id: acmm_tools\n"
        "      name: ACMM Tools\n"
        "      description: Package-local controlled tools.\n"
        "      source: tools/acmm_tools.py\n",
        encoding="utf-8",
    )
    open_webui_tool.write_text("VALUE = 1\n", encoding="utf-8")

    agent_zero_root = root / "apparatus" / "agent-zero"
    agent_zero_plugin = agent_zero_root / "plugins" / "acmm_tools"
    agent_zero_plugin.mkdir(parents=True)
    (agent_zero_root / "apparatus.yaml").write_text(
        'schema_version: "0.1"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "tool_policy:\n"
        "  allowed:\n"
        "    - plugin:acmm_tools:get_magic_number\n"
        "plugins:\n"
        "  managed:\n"
        "    - name: acmm_tools\n"
        "      source: plugins/acmm_tools\n"
        "      destination: plugins/acmm_tools\n",
        encoding="utf-8",
    )
    (agent_zero_plugin / "plugin.yaml").write_text(
        "name: acmm_tools\n",
        encoding="utf-8",
    )


def _acquire(tmp_path: Path):
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_apparatus_package(source)
    package = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    return source, package


def test_staged_apparatus_composes_package_local_sources(tmp_path: Path) -> None:
    source, package = _acquire(tmp_path)
    shutil.rmtree(source)

    hermes = load_staged_experiment_apparatus(package, "hermes")
    hermes_profile = with_hermes_apparatus(
        hermes.root,
        load_hermes_profile(ROOT),
        EXPERIMENT_ID,
    )
    assert hermes_profile.plugins[0].source == Path("plugins/acmm-tools")
    assert hermes_profile.plugins[0].source_root == hermes.root
    assert (hermes.root / hermes_profile.plugins[0].source).is_dir()

    open_webui = load_staged_experiment_apparatus(package, "open-webui")
    open_webui_profile = with_open_webui_apparatus(
        open_webui.root,
        load_open_webui_profile(ROOT),
        EXPERIMENT_ID,
    )
    assert open_webui_profile.tools[0].source == Path("tools/acmm_tools.py")
    assert open_webui_profile.tools[0].source_root == open_webui.root
    assert (open_webui.root / open_webui_profile.tools[0].source).is_file()

    agent_zero = load_staged_experiment_apparatus(package, "agent-zero")
    agent_zero_profile = with_agent_zero_apparatus(
        agent_zero.root,
        load_agent_zero_profile(ROOT),
        EXPERIMENT_ID,
    )
    assert agent_zero_profile.plugins[0].source == Path("plugins/acmm_tools")
    assert agent_zero_profile.plugins[0].source_root == agent_zero.root
    assert (agent_zero.root / agent_zero_profile.plugins[0].source).is_dir()


def test_apparatus_resolution_requires_staged_package(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_apparatus_package(source)
    package = load_experiment_package(
        source,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    with pytest.raises(experiment_apparatus_error, match="ACMM-staged"):
        load_staged_experiment_apparatus(package, "hermes")


def test_apparatus_resolution_detects_staged_tampering(tmp_path: Path) -> None:
    _, package = _acquire(tmp_path)
    component = package.apparatus["open-webui"]
    (component.root / "tools" / "acmm_tools.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )

    with pytest.raises(experiment_apparatus_error, match="changed after acquisition"):
        load_staged_experiment_apparatus(package, "open-webui")


def test_apparatus_resolution_rejects_undeclared_subject(tmp_path: Path) -> None:
    _, package = _acquire(tmp_path)

    with pytest.raises(experiment_apparatus_error, match="does not declare apparatus"):
        load_staged_experiment_apparatus(package, "unknown-agent")


def test_agent_managers_resolve_sources_from_staged_apparatus_root(tmp_path: Path) -> None:
    _, package = _acquire(tmp_path)

    hermes_component = load_staged_experiment_apparatus(package, "hermes")
    hermes_profile = with_hermes_apparatus(
        hermes_component.root,
        load_hermes_profile(ROOT),
        EXPERIMENT_ID,
    )
    hermes_provenance = apparatus_provenance(package, "hermes")
    hermes = hermes_manager(
        ROOT,
        tmp_path / "hermes-runtime",
        hermes_profile,
        object(),
        experiment_id=EXPERIMENT_ID,
        deployment_provenance=hermes_provenance,
    )
    assert hermes._plugin_source(hermes_profile.plugins[0]) == (
        hermes_component.root / "plugins" / "acmm-tools"
    )
    assert hermes._apparatus_source_sha256("hermes", EXPERIMENT_ID) == (
        hermes_component.sha256
    )

    open_webui_component = load_staged_experiment_apparatus(package, "open-webui")
    open_webui_profile = with_open_webui_apparatus(
        open_webui_component.root,
        load_open_webui_profile(ROOT),
        EXPERIMENT_ID,
    )
    open_webui_provenance = apparatus_provenance(package, "open-webui")
    open_webui = open_webui_manager(
        ROOT,
        tmp_path / "open-webui-runtime",
        open_webui_profile,
        object(),
        api=object(),
        experiment_id=EXPERIMENT_ID,
        deployment_provenance=open_webui_provenance,
    )
    payload = open_webui._tool_payload(open_webui_profile.tools[0])
    assert payload["content"] == "VALUE = 1\n"
    assert open_webui._apparatus_source_sha256("open-webui", EXPERIMENT_ID) == (
        open_webui_component.sha256
    )

    agent_zero_component = load_staged_experiment_apparatus(package, "agent-zero")
    agent_zero_profile = with_agent_zero_apparatus(
        agent_zero_component.root,
        load_agent_zero_profile(ROOT),
        EXPERIMENT_ID,
    )
    agent_zero_provenance = apparatus_provenance(package, "agent-zero")
    agent_zero = agent_zero_manager(
        ROOT,
        tmp_path / "agent-zero-runtime",
        agent_zero_profile,
        object(),
        api=object(),
        experiment_id=EXPERIMENT_ID,
        deployment_provenance=agent_zero_provenance,
    )
    assert agent_zero._plugin_source(agent_zero_profile.plugins[0]) == (
        agent_zero_component.root / "plugins" / "acmm_tools"
    )
    assert agent_zero._apparatus_source_sha256("agent-zero", EXPERIMENT_ID) == (
        agent_zero_component.sha256
    )


def test_deployed_manager_reopens_package_from_persisted_provenance(
    tmp_path: Path,
) -> None:
    _, package = _acquire(tmp_path)
    runtime_root = tmp_path / "runtime-root"
    initial = build_package_subject_manager(
        ROOT,
        runtime_root,
        "hermes",
        package,
    )
    provenance = apparatus_provenance(package, "hermes")
    write_experiment_apparatus_state(
        initial.manager.deployment_root,
        experiment_id=EXPERIMENT_ID,
        source_sha256=provenance.apparatus_sha256,
        application_scope="experiment",
        resources=[],
        package_provenance=provenance,
    )

    reopened = build_deployed_subject_manager(
        ROOT,
        runtime_root,
        "hermes",
    )

    assert reopened.experiment_id == EXPERIMENT_ID
    assert reopened.package_sha256 == package.package_sha256
    assert reopened.llm_baseline_id == "nemotron_3_nano_30b_cloud_v1"
    assert reopened.manager.deployment_provenance == provenance
    assert reopened.manager.profile.plugins[0].source_root == (
        package.apparatus["hermes"].root
    )


def test_package_deployment_provenance_supports_no_apparatus_baseline(
    tmp_path: Path,
) -> None:
    external_experiment_id = "external_baseline_probe_v1"
    source = tmp_path / "baseline-source"
    source.mkdir(parents=True)
    contract_text = VALID_CONTRACT.read_text(encoding="utf-8").replace(
        EXPERIMENT_ID,
        external_experiment_id,
        1,
    )
    (source / "experiment.yaml").write_text(contract_text, encoding="utf-8")
    (source / "experiment-package.yaml").write_text(
        'schema_version: "0.1"\n'
        f"experiment_id: {external_experiment_id}\n"
        "contract: experiment.yaml\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "baseline-runtime"
    package = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    context = build_package_subject_manager(
        ROOT,
        runtime_root,
        "hermes",
        package,
    )
    provenance = context.manager.deployment_provenance
    assert provenance is not None
    assert provenance.apparatus_sha256 is None
    assert context.manager.profile.plugins == ()

    write_experiment_apparatus_state(
        context.manager.deployment_root,
        experiment_id=external_experiment_id,
        source_sha256=None,
        application_scope="experiment",
        resources=[],
        package_provenance=provenance,
    )

    reopened = build_deployed_subject_manager(
        ROOT,
        runtime_root,
        "hermes",
    )
    assert reopened.package_sha256 == package.package_sha256
    assert reopened.manager.deployment_provenance == provenance
    assert reopened.manager.profile.plugins == ()

    observed = inspect_subject_context(
        ROOT,
        runtime_root,
        cohort_subject(
            "hermes_nemotron30b",
            "hermes",
            "nemotron_3_nano_30b_cloud_v1",
        ),
        include_running=False,
    )
    assert observed.package_sha256 == package.package_sha256
    assert observed.apparatus_state == "match"
    assert observed.source_sha256 is None
