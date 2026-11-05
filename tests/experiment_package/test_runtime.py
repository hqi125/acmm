from __future__ import annotations

from pathlib import Path
import shutil

import pytest

import acmm.experiment_api as experiment_api
from acmm.experiment_package import (
    acquire_experiment_package,
    compose_execution_registry,
    experiment_runtime_error,
    load_experiment_package,
    load_staged_experiment_runtime,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = ROOT / "contracts" / "v0_1" / "contract.schema.json"
VALID_CONTRACT = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "package_fixture.yaml"
EXPERIMENT_ID = "package_fixture_v1"


_RUNTIME_COMPONENTS = '''from __future__ import annotations

from acmm.experiment_api import (
    evaluation_context,
    judgment_result,
    prepared_environment,
)


class package_fixture:
    def __init__(self, marker: str) -> None:
        self.marker = marker

    def prepare(self, scenario, reset):
        del scenario
        del reset
        return prepared_environment(handle={"marker": self.marker}, provenance={})


class package_evaluator:
    def __init__(self, name: str) -> None:
        self.name = name

    def evaluate(self, context: evaluation_context) -> judgment_result:
        del context
        return judgment_result(outcome="compliant", evidence_ids=(), detail=self.name)
'''


_RUNTIME_INIT = '''from acmm.experiment_api import experiment_runtime, harness_capabilities

from .components import package_evaluator, package_fixture


def build_runtime(context):
    marker = context.resolve_resource("assets/marker.txt").read_text(encoding="utf-8").strip()
    return experiment_runtime(
        capabilities=harness_capabilities(
            evaluators=frozenset({"fixture_value_retrieval", "fixture_value_addition"}),
            fixtures=frozenset({"package_fixture_environment_v1"}),
            evidence_channels=frozenset(
                {"conversation", "tool_calls", "tool_results", "final_response"}
            ),
        ),
        fixtures={"package_fixture_environment_v1": package_fixture(marker)},
        evaluators={
            "fixture_value_retrieval": package_evaluator("retrieval"),
            "fixture_value_addition": package_evaluator("addition"),
        },
    )
'''


def _write_runtime_package(
    root: Path,
    *,
    runtime_init: str = _RUNTIME_INIT,
) -> None:
    root.mkdir(parents=True)
    shutil.copy2(VALID_CONTRACT, root / "experiment.yaml")
    (root / "experiment-package.yaml").write_text(
        'schema_version: "0.1"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "contract: experiment.yaml\n"
        "runtime:\n"
        '  api_version: "0.1"\n'
        "  root: runtime\n"
        "  entrypoint: build_runtime\n",
        encoding="utf-8",
    )
    runtime_root = root / "runtime"
    runtime_root.mkdir()
    (runtime_root / "__init__.py").write_text(runtime_init, encoding="utf-8")
    (runtime_root / "components.py").write_text(_RUNTIME_COMPONENTS, encoding="utf-8")
    assets_root = root / "assets"
    assets_root.mkdir()
    (assets_root / "marker.txt").write_text("staged-runtime\n", encoding="utf-8")


def _acquire(tmp_path: Path, *, runtime_init: str = _RUNTIME_INIT):
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_runtime_package(source, runtime_init=runtime_init)
    return acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )


def test_public_experiment_api_does_not_expose_subject_adapters_or_registry() -> None:
    assert not hasattr(experiment_api, "subject_adapter")
    assert not hasattr(experiment_api, "execution_registry")


def test_runtime_activation_requires_staged_package(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_runtime_package(source)
    package = load_experiment_package(
        source,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    with pytest.raises(experiment_runtime_error, match="ACMM-staged"):
        load_staged_experiment_runtime(package)


def test_staged_runtime_loads_relative_modules_and_package_resources(tmp_path: Path) -> None:
    package = _acquire(tmp_path)

    loaded = load_staged_experiment_runtime(package)

    assert loaded.context.experiment_id == EXPERIMENT_ID
    assert loaded.context.package_sha256 == package.package_sha256
    assert loaded.context.package_root == package.root
    assert loaded.module_name.startswith(f"_acmm_experiment_{EXPERIMENT_ID}_")
    assert set(loaded.runtime.fixtures) == {"package_fixture_environment_v1"}
    assert set(loaded.runtime.evaluators) == {
        "fixture_value_retrieval",
        "fixture_value_addition",
    }
    fixture = loaded.runtime.fixtures["package_fixture_environment_v1"]
    assert fixture.marker == "staged-runtime"
    assert package.runtime is not None
    assert not (package.runtime.root / "__pycache__").exists()


def test_runtime_context_rejects_unsafe_resource_paths(tmp_path: Path) -> None:
    package = _acquire(tmp_path)
    loaded = load_staged_experiment_runtime(package)

    with pytest.raises(ValueError, match="package-relative and safe"):
        loaded.context.resolve_resource("../outside.txt")


def test_runtime_composes_with_core_owned_adapters_and_overlays(tmp_path: Path) -> None:
    package = _acquire(tmp_path)
    loaded = load_staged_experiment_runtime(package)
    adapter = object()

    registry = compose_execution_registry(
        loaded.runtime,
        {"subject_a": adapter},
        subject_configuration_overlays={
            "subject_a": {"llm": {"baseline_id": "baseline_a"}},
        },
    )

    assert registry.subject_adapters == {"subject_a": adapter}
    assert registry.fixtures == dict(loaded.runtime.fixtures)
    assert registry.evaluators == dict(loaded.runtime.evaluators)
    assert registry.subject_configuration_overlays == {
        "subject_a": {"llm": {"baseline_id": "baseline_a"}},
    }


def test_runtime_entrypoint_must_return_public_runtime_type(tmp_path: Path) -> None:
    package = _acquire(
        tmp_path,
        runtime_init="def build_runtime(context):\n    return context\n",
    )

    with pytest.raises(experiment_runtime_error, match="must return experiment_runtime"):
        load_staged_experiment_runtime(package)


def test_runtime_declarations_must_match_capabilities(tmp_path: Path) -> None:
    runtime_init = _RUNTIME_INIT.replace(
        'fixtures={"package_fixture_environment_v1": package_fixture(marker)},',
        "fixtures={},",
    )
    package = _acquire(tmp_path, runtime_init=runtime_init)

    with pytest.raises(experiment_runtime_error, match="fixture declarations"):
        load_staged_experiment_runtime(package)


def test_runtime_capabilities_must_match_contract(tmp_path: Path) -> None:
    runtime_init = _RUNTIME_INIT.replace(
        'evaluators=frozenset({"fixture_value_retrieval", "fixture_value_addition"}),',
        'evaluators=frozenset({"fixture_value_retrieval"}),',
    ).replace(
        '            "fixture_value_addition": package_evaluator("addition"),\n',
        "",
    )
    package = _acquire(tmp_path, runtime_init=runtime_init)

    with pytest.raises(experiment_runtime_error, match="incompatible with its Contract"):
        load_staged_experiment_runtime(package)


def test_runtime_activation_detects_staged_package_tampering(tmp_path: Path) -> None:
    package = _acquire(tmp_path)
    assert package.runtime is not None
    (package.runtime.root / "components.py").write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(experiment_runtime_error, match="changed before runtime activation"):
        load_staged_experiment_runtime(package)


def test_runtime_entrypoint_must_not_mutate_managed_package_content(tmp_path: Path) -> None:
    runtime_init = _RUNTIME_INIT.replace(
        "def build_runtime(context):\n",
        "def build_runtime(context):\n"
        "    context.resolve_resource('assets/marker.txt').write_text(\n"
        "        'changed\\n', encoding='utf-8'\n"
        "    )\n",
    )
    package = _acquire(tmp_path, runtime_init=runtime_init)

    with pytest.raises(experiment_runtime_error, match="changed before runtime activation"):
        load_staged_experiment_runtime(package)
