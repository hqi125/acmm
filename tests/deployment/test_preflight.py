from pathlib import Path

from acmm.deployment_preflight import (
    discover_deployment_contexts,
    inspect_package_deployment_contexts,
)
from acmm.experiment_package import (
    deployment_provenance,
    install_experiment_package,
)
from acmm.experiment_package.provenance import (
    experiment_deployment_provenance,
    experiment_package_provenance,
)
from acmm.subjects.catalog import lookup_subject_execution_binding
from acmm_infra.deployment import write_experiment_apparatus_state



ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = ROOT / "contracts" / "v0_1" / "contract.schema.json"
PACKAGE_SMOKE_CONTRACT = (
    ROOT / "tests" / "fixtures" / "contracts" / "valid" / "package_smoke.yaml"
)


def _install_test_package(tmp_path: Path, monkeypatch):
    import shutil
    import yaml
    from acmm.experiment_package import install_experiment_package

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    source = tmp_path / "package-source"
    source.mkdir()
    shutil.copy2(PACKAGE_SMOKE_CONTRACT, source / "experiment.yaml")
    contract = yaml.safe_load(PACKAGE_SMOKE_CONTRACT.read_text(encoding="utf-8"))
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
    (runtime / "__init__.py").write_text(
        "from acmm.experiment_api import experiment_runtime, harness_capabilities\n"
        "def build_runtime(context):\n"
        "    del context\n"
        "    return experiment_runtime(capabilities=harness_capabilities(\n"
        "        evaluators=frozenset({'always_compliant'}),\n"
        "        fixtures=frozenset(), tools=frozenset(),\n"
        "        evidence_channels=frozenset({'final_response'}),\n"
        "    ), evaluators={})\n",
        encoding="utf-8",
    )
    return install_experiment_package(
        source, runtime_root, contract_schema_path=CONTRACT_SCHEMA
    ), runtime_root


def _write_package_context(package, runtime_root: Path, subject_id: str) -> None:
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


SUBJECT_ID = "hermes_gpt_oss120b"


def _binding():
    binding = lookup_subject_execution_binding(SUBJECT_ID)
    assert binding is not None
    return binding


def test_package_preflight_rejects_missing_context(tmp_path: Path, monkeypatch) -> None:
    package, runtime_root = _install_test_package(tmp_path, monkeypatch)

    result = inspect_package_deployment_contexts(
        runtime_root=runtime_root,
        package=package,
        subject_bindings={SUBJECT_ID: _binding()},
    )

    assert not result.is_ready
    assert "no active experiment deployment context" in result.subjects[0].issues[0]


def test_package_preflight_accepts_exact_package_without_apparatus(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, runtime_root = _install_test_package(tmp_path, monkeypatch)
    _write_package_context(package, runtime_root, SUBJECT_ID)

    result = inspect_package_deployment_contexts(
        runtime_root=runtime_root,
        package=package,
        subject_bindings={SUBJECT_ID: _binding()},
    )

    assert result.is_ready
    assert result.subjects[0].package_sha256 == package.package_sha256
    assert result.subjects[0].expected_apparatus_sha256 is None
    assert result.subjects[0].observed_apparatus_sha256 is None


def test_discovery_accepts_package_context_without_apparatus(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, runtime_root = _install_test_package(tmp_path, monkeypatch)
    _write_package_context(package, runtime_root, SUBJECT_ID)

    result = discover_deployment_contexts(
        runtime_root=runtime_root,
        subject_bindings={SUBJECT_ID: _binding()},
    )

    assert result.experiment_ids == (package.experiment_id,)
    assert result.package_sha256s == (package.package_sha256,)
    assert result.subjects[0].issues == ()


def test_package_preflight_rejects_different_package_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, runtime_root = _install_test_package(tmp_path, monkeypatch)
    _write_package_context(package, runtime_root, SUBJECT_ID)

    source = tmp_path / "package-source"
    (source / "notes.txt").write_text("changed revision\n", encoding="utf-8")
    requested = install_experiment_package(source, runtime_root, contract_schema_path=CONTRACT_SCHEMA)
    assert requested.package_sha256 != package.package_sha256

    result = inspect_package_deployment_contexts(
        runtime_root=runtime_root,
        package=requested,
        subject_bindings={SUBJECT_ID: _binding()},
    )

    assert not result.is_ready
    assert any("package SHA-256" in issue for issue in result.subjects[0].issues)


def test_package_preflight_rejects_non_package_deployment_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, runtime_root = _install_test_package(tmp_path, monkeypatch)
    write_experiment_apparatus_state(
        runtime_root / "hermes",
        experiment_id=package.experiment_id,
        source_sha256="a" * 64,
        application_scope="experiment",
        resources=[],
    )

    result = inspect_package_deployment_contexts(
        runtime_root=runtime_root,
        package=package,
        subject_bindings={SUBJECT_ID: _binding()},
    )

    assert not result.is_ready
    assert any("not Experiment Package-backed" in issue for issue in result.subjects[0].issues)


def test_discovery_reports_invalid_legacy_marker_without_promoting_it_to_package(
    tmp_path: Path,
) -> None:
    write_experiment_apparatus_state(
        tmp_path / "hermes",
        experiment_id="unsupported_legacy_context_v1",
        source_sha256="a" * 64,
        application_scope="experiment",
        resources=[],
    )

    result = discover_deployment_contexts(
        runtime_root=tmp_path,
        subject_bindings={SUBJECT_ID: _binding()},
    )

    assert result.experiment_ids == ("unsupported_legacy_context_v1",)
    assert result.package_sha256s == ()
