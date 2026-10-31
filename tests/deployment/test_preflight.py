from pathlib import Path

from acmm.deployment_preflight import (
    discover_deployment_contexts,
    inspect_deployment_contexts,
    inspect_package_deployment_contexts,
)
from acmm.experiment_package import acquire_experiment_package, deployment_provenance
from acmm.experiment_package.provenance import (
    experiment_deployment_provenance,
    experiment_package_provenance,
)
from acmm.subjects.catalog import lookup_subject_execution_binding
from acmm_infra.deployment import write_experiment_apparatus_state
from acmm_infra.profiles.experiments import experiment_apparatus_sha256


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "controlled_tool_use_multiscenario_v1"
CONTRACT_SCHEMA = ROOT / "contracts" / "v0_1" / "contract.schema.json"
VALID_CONTRACT = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "magic_number.yaml"


def _hermes_binding():
    binding = lookup_subject_execution_binding("hermes_nemotron30b")
    assert binding is not None
    return binding


def _inspect(tmp_path: Path):
    return inspect_deployment_contexts(
        repo_root=ROOT,
        runtime_root=tmp_path,
        experiment_id=EXPERIMENT_ID,
        subject_bindings={"hermes_nemotron30b": _hermes_binding()},
    )


def test_preflight_accepts_matching_full_experiment_context(tmp_path: Path) -> None:
    write_experiment_apparatus_state(
        tmp_path / "hermes",
        experiment_id=EXPERIMENT_ID,
        source_sha256=experiment_apparatus_sha256(
            ROOT,
            "hermes",
            EXPERIMENT_ID,
        ),
        application_scope="experiment",
        resources=[],
    )

    result = _inspect(tmp_path)

    assert result.is_ready
    assert result.subjects[0].issues == ()


def test_preflight_rejects_missing_context(tmp_path: Path) -> None:
    result = _inspect(tmp_path)

    assert not result.is_ready
    assert "no active experiment deployment context" in result.subjects[0].issues[0]


def test_preflight_rejects_wrong_experiment(tmp_path: Path) -> None:
    write_experiment_apparatus_state(
        tmp_path / "hermes",
        experiment_id="magic_number_tool_use_v1",
        source_sha256=experiment_apparatus_sha256(
            ROOT,
            "hermes",
            "magic_number_tool_use_v1",
        ),
        application_scope="experiment",
        resources=[],
    )

    result = _inspect(tmp_path)

    assert not result.is_ready
    assert any("active experiment is 'magic_number_tool_use_v1'" in issue
               for issue in result.subjects[0].issues)


def test_preflight_rejects_tools_only_context(tmp_path: Path) -> None:
    write_experiment_apparatus_state(
        tmp_path / "hermes",
        experiment_id=EXPERIMENT_ID,
        source_sha256=experiment_apparatus_sha256(
            ROOT,
            "hermes",
            EXPERIMENT_ID,
        ),
        application_scope="tools",
        resources=[],
    )

    result = _inspect(tmp_path)

    assert not result.is_ready
    assert any("application scope is 'tools'" in issue
               for issue in result.subjects[0].issues)


def test_preflight_rejects_stale_apparatus_hash(tmp_path: Path) -> None:
    write_experiment_apparatus_state(
        tmp_path / "hermes",
        experiment_id=EXPERIMENT_ID,
        source_sha256="0" * 64,
        application_scope="experiment",
        resources=[],
    )

    result = _inspect(tmp_path)

    assert not result.is_ready
    assert any("apparatus SHA-256" in issue for issue in result.subjects[0].issues)


def test_preflight_checks_only_selected_subject_bindings(tmp_path: Path) -> None:
    binding = lookup_subject_execution_binding("open_webui_nemotron30b")
    assert binding is not None
    write_experiment_apparatus_state(
        tmp_path / "open-webui",
        experiment_id=EXPERIMENT_ID,
        source_sha256=experiment_apparatus_sha256(
            ROOT,
            "open-webui",
            EXPERIMENT_ID,
        ),
        application_scope="experiment",
        resources=[],
    )

    result = inspect_deployment_contexts(
        repo_root=ROOT,
        runtime_root=tmp_path,
        experiment_id=EXPERIMENT_ID,
        subject_bindings={"open_webui_nemotron30b": binding},
    )

    assert result.is_ready
    assert [subject.subject_id for subject in result.subjects] == [
        "open_webui_nemotron30b"
    ]


def test_discovery_accepts_package_context_without_apparatus(tmp_path: Path) -> None:
    binding = _hermes_binding()
    package_sha256 = "a" * 64
    provenance = experiment_deployment_provenance(
        package=experiment_package_provenance(
            experiment_id=EXPERIMENT_ID,
            package_schema_version="0.1",
            package_sha256=package_sha256,
            contract_sha256="b" * 64,
            runtime_sha256=None,
        ),
        infrastructure_subject="hermes",
        apparatus_sha256=None,
    )
    write_experiment_apparatus_state(
        tmp_path / "hermes",
        experiment_id=EXPERIMENT_ID,
        source_sha256=None,
        application_scope="experiment",
        resources=[],
        package_provenance=provenance,
    )

    result = discover_deployment_contexts(
        runtime_root=tmp_path,
        subject_bindings={"hermes_nemotron30b": binding},
    )

    assert result.experiment_ids == (EXPERIMENT_ID,)
    assert result.package_sha256s == (package_sha256,)
    assert result.subjects[0].issues == ()
    assert result.subjects[0].observed_apparatus_sha256 is None


def test_package_preflight_accepts_exact_package_without_apparatus(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "experiment.yaml").write_bytes(VALID_CONTRACT.read_bytes())
    (source / "experiment-package.yaml").write_text(
        'schema_version: "0.1"\n'
        "experiment_id: magic_number_tool_use_v1\n"
        "contract: experiment.yaml\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    package = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    provenance = deployment_provenance(package, "hermes")
    write_experiment_apparatus_state(
        runtime_root / "hermes",
        experiment_id=package.experiment_id,
        source_sha256=None,
        application_scope="experiment",
        resources=[],
        package_provenance=provenance,
    )

    result = inspect_package_deployment_contexts(
        runtime_root=runtime_root,
        package=package,
        subject_bindings={"hermes_nemotron30b": _hermes_binding()},
    )

    assert result.is_ready
    assert result.subjects[0].package_sha256 == package.package_sha256
    assert result.subjects[0].expected_apparatus_sha256 is None
    assert result.subjects[0].observed_apparatus_sha256 is None


def test_package_preflight_rejects_different_package_revision(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "experiment.yaml").write_bytes(VALID_CONTRACT.read_bytes())
    (source / "experiment-package.yaml").write_text(
        'schema_version: "0.1"\n'
        "experiment_id: magic_number_tool_use_v1\n"
        "contract: experiment.yaml\n",
        encoding="utf-8",
    )
    (source / "notes.txt").write_text("revision one\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    deployed_package = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    write_experiment_apparatus_state(
        runtime_root / "hermes",
        experiment_id=deployed_package.experiment_id,
        source_sha256=None,
        application_scope="experiment",
        resources=[],
        package_provenance=deployment_provenance(deployed_package, "hermes"),
    )

    (source / "notes.txt").write_text("revision two\n", encoding="utf-8")
    requested_package = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    assert requested_package.package_sha256 != deployed_package.package_sha256

    result = inspect_package_deployment_contexts(
        runtime_root=runtime_root,
        package=requested_package,
        subject_bindings={"hermes_nemotron30b": _hermes_binding()},
    )

    assert not result.is_ready
    assert any("package SHA-256" in issue for issue in result.subjects[0].issues)
