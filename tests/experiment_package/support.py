from __future__ import annotations

from pathlib import Path
import shutil

from acmm.experiment_package import (
    deployment_provenance,
    experiment_package,
    install_experiment_package,
)
from acmm.subjects.catalog import lookup_subject_execution_binding
from acmm_infra.deployment import write_experiment_apparatus_state


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = ROOT / "contracts" / "v0_1" / "contract.schema.json"

MAGIC_RUNTIME_SOURCE = '''from acmm.pilots.magic_number import build_magic_number_runtime


def build_runtime(context):
    del context
    return build_magic_number_runtime()
'''

CONTROLLED_TOOL_USE_RUNTIME_SOURCE = '''from acmm.experiment_api import experiment_runtime
from acmm.pilots.controlled_tool_use import (
    addition_tool_use_evaluator,
    authoritative_source_invocation_evaluator,
    controlled_magic_number_fixture,
    controlled_tool_use_harness_capabilities,
    retrieval_prerequisite_evaluator,
    stop_after_tool_failure_evaluator,
    successful_result_fidelity_evaluator,
    tool_failure_disclosure_evaluator,
    unestablished_result_claim_evaluator,
)


def build_runtime(context):
    del context
    return experiment_runtime(
        capabilities=controlled_tool_use_harness_capabilities(),
        fixtures={
            "controlled_magic_number_environment_v1": controlled_magic_number_fixture(),
        },
        evaluators={
            "authoritative_source_invocation": authoritative_source_invocation_evaluator(),
            "retrieval_prerequisite": retrieval_prerequisite_evaluator(),
            "addition_tool_use": addition_tool_use_evaluator(),
            "successful_result_fidelity": successful_result_fidelity_evaluator(),
            "tool_failure_disclosure": tool_failure_disclosure_evaluator(),
            "unestablished_result_claim": unestablished_result_claim_evaluator(),
            "stop_after_tool_failure": stop_after_tool_failure_evaluator(),
        },
    )
'''


def install_test_package(
    tmp_path: Path,
    monkeypatch,
    *,
    contract_path: Path,
    runtime_source: str,
    source_name: str = "package-source",
) -> tuple[experiment_package, Path]:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))

    source = tmp_path / source_name
    source.mkdir(parents=True)
    shutil.copy2(contract_path, source / "experiment.yaml")

    import yaml

    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    experiment_id = contract["experiment"]["id"]
    (source / "experiment-package.yaml").write_text(
        'schema_version: "0.1"\n'
        f"experiment_id: {experiment_id}\n"
        "contract: experiment.yaml\n"
        "runtime:\n"
        '  api_version: "0.1"\n'
        "  root: runtime\n"
        "  entrypoint: build_runtime\n",
        encoding="utf-8",
    )
    runtime = source / "runtime"
    runtime.mkdir()
    (runtime / "__init__.py").write_text(runtime_source, encoding="utf-8")

    package = install_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    return package, runtime_root


def write_package_context(
    package: experiment_package,
    runtime_root: Path,
    subject_id: str,
) -> None:
    binding = lookup_subject_execution_binding(subject_id)
    assert binding is not None
    provenance = deployment_provenance(package, binding.infrastructure_subject)
    write_experiment_apparatus_state(
        runtime_root / binding.infrastructure_subject,
        experiment_id=package.experiment_id,
        source_sha256=provenance.apparatus_sha256,
        application_scope="experiment",
        resources=[],
        package_provenance=provenance,
    )
