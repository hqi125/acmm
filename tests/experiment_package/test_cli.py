from __future__ import annotations

from pathlib import Path
import shutil

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


def test_package_validate_and_acquire_commands(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_package(source)
    parser = _build_parser()

    validate_args = parser.parse_args(["package", "validate", str(source)])
    assert validate_args.handler(validate_args) == 0
    validate_output = capsys.readouterr().out
    assert "Experiment Package is valid:" in validate_output
    assert f"experiment:       {EXPERIMENT_ID}" in validate_output
    assert "source root:" in validate_output

    acquire_args = parser.parse_args(
        [
            "package",
            "acquire",
            str(source),
            "--runtime-root",
            str(runtime_root),
        ]
    )
    assert acquire_args.handler(acquire_args) == 0
    acquire_output = capsys.readouterr().out
    assert "Experiment Package acquired:" in acquire_output
    assert "staged root:" in acquire_output
    assert (runtime_root / "_experiment_packages" / EXPERIMENT_ID).is_dir()


def test_run_package_preview_stages_and_uses_package_runtime(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_package(source)
    parser = _build_parser()

    args = parser.parse_args(
        [
            "run",
            "--package",
            str(source),
            "--runtime-root",
            str(runtime_root),
            "--subject",
            "hermes_nemotron30b",
            "--preview",
        ]
    )
    assert args.handler(args) == 0

    captured = capsys.readouterr()
    assert "source:          Experiment Package" in captured.out
    assert f"experiment:      {EXPERIMENT_ID}" in captured.out
    assert "package SHA-256:" in captured.out
    assert "No execution started (--preview)." in captured.out
    assert "no active experiment deployment context" in captured.err


def test_run_infers_unregistered_package_from_deployment_context(
    tmp_path: Path,
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
            "--runtime-root",
            str(runtime_root),
            "--subject",
            "hermes_nemotron30b",
            "--preview",
        ]
    )
    assert args.handler(args) == 0

    captured = capsys.readouterr()
    assert "deployment context / staged Experiment Package" in captured.out
    assert f"experiment:      {EXPERIMENT_ID}" in captured.out
    assert "apparatus:         match (none declared)" in captured.out
    assert captured.err == ""


def test_environment_apply_package_acquires_and_resolves_package_cohort(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    from acmm import environment_cli
    from acmm_infra.cohort import cohort_subject_status

    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_package(source)
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

    monkeypatch.setattr(environment_cli, "apply_cohort", fake_apply)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "env",
            "apply",
            "--package",
            str(source),
            "--repo-root",
            str(ROOT),
            "--runtime-root",
            str(runtime_root),
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
    assert "package SHA-256:" in output
    assert "staged package:" in output
