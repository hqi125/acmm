from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from acmm.experiment_package import (
    acquire_experiment_package,
    compose_staged_experiment_integrations,
    experiment_integration_error,
    load_experiment_package,
    load_staged_experiment_integration,
)
from acmm_infra.profiles.hermes import load_hermes_profile
from acmm_infra.subject_managers import build_package_subject_manager


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = ROOT / "contracts" / "v0_1" / "contract.schema.json"
VALID_CONTRACT = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "package_fixture.yaml"
EXPERIMENT_ID = "package_fixture_v1"


INTEGRATION_SOURCE = '''from dataclasses import replace

from acmm.integration_api import experiment_integration


class hermes_profile_integration:
    def __init__(self, context):
        self.context = context

    def compose_profile(self, context, profile):
        assert context.integration_id == self.context.integration_id
        assert context.preset == "test_preset_v1"
        assert context.configuration["reasoning_effort"] == "low"
        return replace(profile, reasoning_effort=context.configuration["reasoning_effort"])


def build_integration(context):
    return experiment_integration(
        subjects={"hermes": hermes_profile_integration(context)},
    )
'''


def _write_integration_package(
    root: Path,
    *,
    integration_source: str = INTEGRATION_SOURCE,
) -> None:
    root.mkdir(parents=True)
    shutil.copy2(VALID_CONTRACT, root / "experiment.yaml")
    (root / "experiment-package.yaml").write_text(
        'schema_version: "0.2"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "contract: experiment.yaml\n"
        "integrations:\n"
        "  - id: profile-test\n"
        '    api_version: "0.1"\n'
        "    root: integrations/profile-test\n"
        "    entrypoint: build_integration\n"
        "    preset: test_preset_v1\n"
        "    configuration:\n"
        "      reasoning_effort: low\n",
        encoding="utf-8",
    )
    integration_root = root / "integrations" / "profile-test"
    integration_root.mkdir(parents=True)
    (integration_root / "__init__.py").write_text(
        integration_source,
        encoding="utf-8",
    )


def _acquire(tmp_path: Path):
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_integration_package(source)
    package = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    return source, package, runtime_root


def test_package_schema_v0_2_loads_integration_component(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_integration_package(source)

    package = load_experiment_package(
        source,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    assert package.schema_version == "0.2"
    assert len(package.integrations) == 1
    component = package.integrations[0]
    assert component.integration_id == "profile-test"
    assert component.api_version == "0.1"
    assert component.preset == "test_preset_v1"
    assert component.configuration["reasoning_effort"] == "low"
    assert component.root == source.resolve() / "integrations/profile-test"
    assert len(component.sha256) == 64


def test_integration_activation_requires_staged_package(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_integration_package(source)
    package = load_experiment_package(
        source,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    with pytest.raises(experiment_integration_error, match="ACMM-staged"):
        load_staged_experiment_integration(package, "profile-test")


def test_integration_activation_detects_staged_tampering(tmp_path: Path) -> None:
    _, package, _ = _acquire(tmp_path)
    component = package.integrations[0]
    (component.root / "__init__.py").write_text(
        INTEGRATION_SOURCE + "\nVALUE = 2\n",
        encoding="utf-8",
    )

    with pytest.raises(experiment_integration_error, match="changed before integration"):
        load_staged_experiment_integration(package, "profile-test")


def test_integration_composes_subject_profile_from_staged_code(tmp_path: Path) -> None:
    source, package, _ = _acquire(tmp_path)
    shutil.rmtree(source)
    baseline = load_hermes_profile(ROOT)

    composed = compose_staged_experiment_integrations(
        package,
        "hermes",
        baseline.llm_baseline_id,
        baseline,
    )

    assert composed.reasoning_effort == "low"
    assert baseline.reasoning_effort != composed.reasoning_effort


def test_package_subject_manager_applies_generic_integration(tmp_path: Path) -> None:
    _, package, runtime_root = _acquire(tmp_path)

    context = build_package_subject_manager(
        ROOT,
        runtime_root,
        "hermes",
        package,
    )

    assert context.manager.profile.reasoning_effort == "low"
    assert context.package_sha256 == package.package_sha256


def test_integration_must_preserve_subject_profile_type(tmp_path: Path) -> None:
    source = '''from acmm.integration_api import experiment_integration


class bad_integration:
    def compose_profile(self, context, profile):
        del context, profile
        return {"not": "a profile"}


def build_integration(context):
    del context
    return experiment_integration(subjects={"hermes": bad_integration()})
'''
    package_root = tmp_path / "source"
    _write_integration_package(package_root, integration_source=source)
    package = acquire_experiment_package(
        package_root,
        tmp_path / "runtime-root",
        contract_schema_path=CONTRACT_SCHEMA,
    )
    baseline = load_hermes_profile(ROOT)

    with pytest.raises(experiment_integration_error, match="preserve the subject profile type"):
        compose_staged_experiment_integrations(
            package,
            "hermes",
            baseline.llm_baseline_id,
            baseline,
        )
