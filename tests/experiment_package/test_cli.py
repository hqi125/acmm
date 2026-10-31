from __future__ import annotations

from pathlib import Path
import shutil

import pytest
import yaml

from acmm.cli import _build_parser
from acmm.experiment_package import (
    acquire_experiment_package,
    deployment_provenance,
)
from acmm_infra.deployment import write_experiment_apparatus_state


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = ROOT / "contracts" / "v0_1" / "contract.schema.json"
VALID_CONTRACT = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "magic_number.yaml"
EXPERIMENT_ID = "external_package_cli_v1"


_RUNTIME_INIT = '''from acmm.experiment_api import (
    experiment_runtime,
    harness_capabilities,
    prepared_environment,
    judgment_result,
)


class fixture:
    def prepare(self, scenario, reset):
        del scenario
        del reset
        return prepared_environment(handle={}, provenance={})


class evaluator:
    def evaluate(self, context):
        del context
        return judgment_result(outcome="compliant", evidence_ids=(), detail="ok")


def build_runtime(context):
    del context
    return experiment_runtime(
        capabilities=harness_capabilities(
            evaluators=frozenset({"magic_number_retrieval", "magic_number_addition"}),
            fixtures=frozenset({"magic_number_environment_v1"}),
            tools=frozenset({"get_magic_number", "add_numbers"}),
            evidence_channels=frozenset(
                {"conversation", "tool_calls", "tool_results", "final_response"}
            ),
        ),
        fixtures={"magic_number_environment_v1": fixture()},
        evaluators={
            "magic_number_retrieval": evaluator(),
            "magic_number_addition": evaluator(),
        },
    )
'''


def _write_package(root: Path) -> None:
    root.mkdir(parents=True)
    contract = yaml.safe_load(VALID_CONTRACT.read_text(encoding="utf-8"))
    contract["experiment"]["id"] = EXPERIMENT_ID
    (root / "experiment.yaml").write_text(
        yaml.safe_dump(contract, sort_keys=False),
        encoding="utf-8",
    )
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
    (runtime_root / "__init__.py").write_text(_RUNTIME_INIT, encoding="utf-8")


def test_package_validate_install_query_and_uninstall_commands(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    _write_package(source)
    parser = _build_parser()

    validate_args = parser.parse_args(["package", "validate", str(source)])
    assert validate_args.handler(validate_args) == 0
    validate_output = capsys.readouterr().out
    assert "Experiment Package is valid:" in validate_output
    assert f"experiment:       {EXPERIMENT_ID}" in validate_output
    assert "source root:" in validate_output

    install_args = parser.parse_args(["package", "install", str(source)])
    assert install_args.handler(install_args) == 0
    install_output = capsys.readouterr().out
    assert "Experiment Package installed:" in install_output
    assert "installed root:" in install_output
    assert (runtime_root / "_experiment_packages" / EXPERIMENT_ID).is_dir()

    list_args = parser.parse_args(["package", "list"])
    assert list_args.handler(list_args) == 0
    list_output = capsys.readouterr().out
    assert "Installed Experiment Packages:" in list_output
    assert EXPERIMENT_ID in list_output

    package_root = next((runtime_root / "_experiment_packages" / EXPERIMENT_ID).iterdir())
    selector = f"{EXPERIMENT_ID}@{package_root.name[:12]}"
    show_args = parser.parse_args(["package", "show", selector])
    assert show_args.handler(show_args) == 0
    show_output = capsys.readouterr().out
    assert "Installed Experiment Package:" in show_output
    assert f"selector:         {selector}" in show_output

    uninstall_args = parser.parse_args(["package", "uninstall", selector])
    assert uninstall_args.handler(uninstall_args) == 0
    uninstall_output = capsys.readouterr().out
    assert "Experiment Package uninstalled:" in uninstall_output
    assert not package_root.exists()


def test_package_uninstall_rejects_deployed_revision(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from acmm.experiment_package import cli as package_cli

    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    _write_package(source)
    package = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    monkeypatch.setattr(
        package_cli,
        "package_deployment_references",
        lambda *args, **kwargs: ("hermes",),
    )

    parser = _build_parser()
    args = parser.parse_args(
        ["package", "uninstall", package.experiment_id]
    )
    assert args.handler(args) == 2
    assert package.root.exists()
    assert "Cannot uninstall" in capsys.readouterr().err


def test_run_installed_package_preview_uses_package_runtime(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_package(source)
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    package = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    parser = _build_parser()

    args = parser.parse_args(
        [
            "run",
            EXPERIMENT_ID,
            "--subject",
            "hermes_nemotron30b",
            "--preview",
        ]
    )
    assert args.handler(args) == 0

    captured = capsys.readouterr()
    assert "installed Experiment Package" in captured.out
    assert EXPERIMENT_ID in captured.out
    assert "package SHA-256:" in captured.out
    assert "No execution started (--preview)." in captured.out
    assert "no active experiment deployment context" in captured.err


def test_run_installed_unregistered_package_uses_explicit_selector(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_package(source)
    package = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    shutil.rmtree(source)

    provenance = deployment_provenance(package, "hermes")
    write_experiment_apparatus_state(
        runtime_root / "hermes",
        experiment_id=EXPERIMENT_ID,
        source_sha256=None,
        application_scope="experiment",
        resources=[],
        package_provenance=provenance,
    )

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            EXPERIMENT_ID,
            "--subject",
            "hermes_nemotron30b",
            "--preview",
        ]
    )
    assert args.handler(args) == 0

    captured = capsys.readouterr()
    assert "installed Experiment Package" in captured.out
    assert EXPERIMENT_ID in captured.out
    assert "apparatus:         match (none declared)" in captured.out
    assert captured.err == ""


def test_package_apply_resolves_installed_package_cohort(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    from acmm.experiment_package import cli as package_cli
    from acmm_infra.cohort import cohort_subject_status

    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    _write_package(source)
    package = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    observed = {}

    def fake_apply(repo_root, observed_runtime_root, cohort, **kwargs):
        del repo_root, kwargs
        observed["runtime_root"] = Path(observed_runtime_root)
        observed["cohort"] = cohort
        subject = cohort.subjects[0]
        return (
            cohort_subject_status(
                subject_id=subject.subject_id,
                infrastructure_subject=subject.infrastructure_subject,
                running=False,
                experiment_id=cohort.experiment_id,
                application_scope="experiment",
                apparatus_state="match",
                source_sha256=None,
                package_sha256=cohort.package.package_sha256,
            ),
        )

    monkeypatch.setattr(package_cli, "apply_cohort", fake_apply)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "package",
            "apply",
            package.experiment_id,
            "--subject",
            "hermes_nemotron30b",
        ]
    )
    assert args.handler(args) == 0

    cohort = observed["cohort"]
    assert cohort.experiment_id == EXPERIMENT_ID
    assert cohort.package is not None
    assert cohort.package.root.parent.name == EXPERIMENT_ID
    assert observed["runtime_root"] == runtime_root
    output = capsys.readouterr().out
    assert "Experiment Package apply:" in output
    assert "package SHA-256:" in output
    assert "Experiment Package apply complete:" in output


@pytest.mark.parametrize(
    "argv",
    (
        ("package", "validate", ".", "--schema", "/tmp/schema.json"),
        ("package", "install", ".", "--runtime-root", "/tmp/runtime"),
        ("package", "list", "--runtime-root", "/tmp/runtime"),
    ),
)
def test_package_parser_rejects_retired_configuration_overrides(
    argv: tuple[str, ...],
) -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(list(argv))
    assert exc_info.value.code == 2
