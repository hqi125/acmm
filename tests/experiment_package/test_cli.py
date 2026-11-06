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
from acmm_infra.deployment import write_experiment_package_state


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = ROOT / "contracts" / "v0_1" / "contract.schema.json"
VALID_CONTRACT = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "package_fixture.yaml"
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
            evaluators=frozenset({"fixture_value_retrieval", "fixture_value_addition"}),
            fixtures=frozenset({"package_fixture_environment_v1"}),
            evidence_channels=frozenset(
                {"conversation", "tool_calls", "tool_results", "final_response"}
            ),
        ),
        fixtures={"package_fixture_environment_v1": fixture()},
        evaluators={
            "fixture_value_retrieval": evaluator(),
            "fixture_value_addition": evaluator(),
        },
    )
'''


def _write_package(root: Path, *, experiment_id: str = EXPERIMENT_ID) -> None:
    root.mkdir(parents=True)
    contract = yaml.safe_load(VALID_CONTRACT.read_text(encoding="utf-8"))
    contract["experiment"]["id"] = experiment_id
    (root / "experiment.yaml").write_text(
        yaml.safe_dump(contract, sort_keys=False),
        encoding="utf-8",
    )
    (root / "experiment-package.yaml").write_text(
        'schema_version: "0.1"\n'
        f"experiment_id: {experiment_id}\n"
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
    assert "[1]" in list_output
    assert EXPERIMENT_ID in list_output

    package_root = next((runtime_root / "_experiment_packages" / EXPERIMENT_ID).iterdir())
    selector = f"{EXPERIMENT_ID}@{package_root.name[:12]}"
    show_args = parser.parse_args(["package", "show", selector])
    assert show_args.handler(show_args) == 0
    show_output = capsys.readouterr().out
    assert "Installed Experiment Package:" in show_output
    assert f"selector:         {selector}" in show_output
    assert "attached instances: 0" in show_output

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
            "hermes_gpt_oss120b",
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
    write_experiment_package_state(
        runtime_root / "hermes",
        experiment_id=EXPERIMENT_ID,
        resources=[],
        package_provenance=provenance,
    )

    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            EXPERIMENT_ID,
            "--subject",
            "hermes_gpt_oss120b",
            "--preview",
        ]
    )
    assert args.handler(args) == 0

    captured = capsys.readouterr()
    assert "installed Experiment Package" in captured.out
    assert EXPERIMENT_ID in captured.out
    assert "context:         ready" in captured.out
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
                package_state="match",
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
            "hermes_gpt_oss120b",
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


def test_package_detach_reports_detached_subjects(
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
        "_detach_installed_package",
        lambda *args, **kwargs: ("hermes_gpt_oss120b",),
    )

    parser = _build_parser()
    args = parser.parse_args(["package", "detach", package.experiment_id])

    assert args.handler(args) == 0
    output = capsys.readouterr().out
    assert "Experiment Package detach complete:" in output
    assert "subjects detached: 1" in output
    assert "hermes_gpt_oss120b" in output


def test_package_uninstall_can_detach_applied_revision(
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
    references = iter((("hermes",), ()))
    monkeypatch.setattr(
        package_cli,
        "package_deployment_references",
        lambda *args, **kwargs: next(references),
    )
    monkeypatch.setattr(
        package_cli,
        "_detach_installed_package",
        lambda *args, **kwargs: ("hermes_gpt_oss120b",),
    )

    parser = _build_parser()
    args = parser.parse_args(
        [
            "package",
            "uninstall",
            package.experiment_id,
            "--detach-if-applied",
        ]
    )

    assert args.handler(args) == 0
    assert not package.root.exists()
    output = capsys.readouterr().out
    assert "Experiment Package detached before uninstall:" in output
    assert "hermes_gpt_oss120b" in output
    assert "Experiment Package uninstalled:" in output


def test_package_install_same_experiment_from_different_paths_does_not_overwrite(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    first_source = tmp_path / "first-source"
    second_source = tmp_path / "second-source"
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    _write_package(first_source)
    _write_package(second_source)
    (second_source / "runtime" / "__init__.py").write_text(
        _RUNTIME_INIT + "\n# second source revision\n",
        encoding="utf-8",
    )

    parser = _build_parser()
    first_args = parser.parse_args(["package", "install", str(first_source)])
    second_args = parser.parse_args(["package", "install", str(second_source)])

    assert first_args.handler(first_args) == 0
    first_output = capsys.readouterr().out
    assert second_args.handler(second_args) == 0
    second_output = capsys.readouterr().out
    installed = [
        item
        for item in package_cli_store_list(runtime_root)
        if item.experiment_id == EXPERIMENT_ID
    ]
    assert len(installed) == 2
    assert installed[0].package_sha256 != installed[1].package_sha256
    assert "Experiment Package installed:" in first_output
    assert "Experiment Package installed:" in second_output


def test_package_install_upgrade_requires_existing_revision(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    _write_package(source)

    parser = _build_parser()
    args = parser.parse_args(["package", "install", str(source), "--upgrade"])

    assert args.handler(args) == 2
    assert "no installed revision exists" in capsys.readouterr().err
    assert not (runtime_root / "_experiment_packages" / EXPERIMENT_ID).exists()


def test_package_install_upgrade_replaces_unattached_revisions(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    _write_package(source)
    first = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    (source / "runtime" / "__init__.py").write_text(
        _RUNTIME_INIT + "\n# upgraded\n",
        encoding="utf-8",
    )

    parser = _build_parser()
    args = parser.parse_args(["package", "install", str(source), "--upgrade"])

    assert args.handler(args) == 0
    installed = [
        item
        for item in package_cli_store_list(runtime_root)
        if item.experiment_id == EXPERIMENT_ID
    ]
    assert len(installed) == 1
    assert installed[0].package_sha256 != first.package_sha256
    output = capsys.readouterr().out
    assert "Experiment Package upgrade complete:" in output
    assert "subjects moved:   0" in output
    assert "revisions purged: 1" in output


def package_cli_store_list(runtime_root: Path):
    from acmm.experiment_package import list_installed_experiment_packages

    return list_installed_experiment_packages(runtime_root)


def test_package_install_upgrade_moves_only_old_revision_subjects(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from acmm.experiment_package import cli as package_cli
    from acmm_infra.cohort import cohort_subject, cohort_subject_status, experiment_cohort

    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    _write_package(source)
    old = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    (source / "runtime" / "__init__.py").write_text(
        _RUNTIME_INIT + "\n# upgraded\n",
        encoding="utf-8",
    )

    attached_subject = cohort_subject(
        subject_id="hermes_gpt_oss120b",
        infrastructure_subject="hermes",
    )
    old_references = {old.package_sha256: ("hermes",)}
    observed: dict[str, object] = {}

    def fake_references(repo_root, observed_runtime_root, package_sha256):
        del repo_root, observed_runtime_root
        return old_references.get(package_sha256, ())

    def fake_attached(repo_root, observed_runtime_root, package, selected_subject_ids=()):
        del repo_root, observed_runtime_root, selected_subject_ids
        if package.package_sha256 != old.package_sha256:
            return experiment_cohort(None, None, ())
        return experiment_cohort(None, None, (attached_subject,))

    def fake_apply(repo_root, observed_runtime_root, cohort, **kwargs):
        del repo_root, observed_runtime_root, kwargs
        observed["subject_ids"] = tuple(item.subject_id for item in cohort.subjects)
        old_references[old.package_sha256] = ()
        return (
            cohort_subject_status(
                subject_id="hermes_gpt_oss120b",
                infrastructure_subject="hermes",
                running=False,
                experiment_id=cohort.experiment_id,
                package_state="match",
                package_sha256=cohort.package.package_sha256,
            ),
        )

    monkeypatch.setattr(package_cli, "package_deployment_references", fake_references)
    monkeypatch.setattr(package_cli, "resolve_attached_package_cohort", fake_attached)
    monkeypatch.setattr(package_cli, "apply_cohort", fake_apply)
    monkeypatch.setattr(package_cli, "apply_staged_integration_resources", lambda *a, **k: ())
    monkeypatch.setattr(
        package_cli,
        "remove_superseded_staged_integration_resources",
        lambda *a, **k: None,
    )

    parser = _build_parser()
    args = parser.parse_args(["package", "install", str(source), "--upgrade"])

    assert args.handler(args) == 0
    assert observed["subject_ids"] == ("hermes_gpt_oss120b",)
    installed = [
        item
        for item in package_cli_store_list(runtime_root)
        if item.experiment_id == EXPERIMENT_ID
    ]
    assert len(installed) == 1
    assert installed[0].package_sha256 != old.package_sha256
    assert "subjects moved:   1" in capsys.readouterr().out


def test_package_show_reports_attached_subject_instances(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from acmm.experiment_package import cli as package_cli
    from acmm_infra.cohort import cohort_subject, experiment_cohort

    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    _write_package(source)
    package = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    subjects = (
        cohort_subject(
            subject_id="hermes_gpt_oss120b",
            infrastructure_subject="hermes",
        ),
        cohort_subject(
            subject_id="agent_zero_gpt_oss120b",
            infrastructure_subject="agent-zero",
        ),
    )
    monkeypatch.setattr(
        package_cli,
        "resolve_attached_package_cohort",
        lambda *args, **kwargs: experiment_cohort(None, None, subjects),
    )
    monkeypatch.setattr(
        package_cli,
        "package_deployment_references",
        lambda *args, **kwargs: ("hermes", "agent-zero"),
    )

    parser = _build_parser()
    args = parser.parse_args(["package", "show", package.experiment_id])

    assert args.handler(args) == 0
    output = capsys.readouterr().out
    assert "attached instances: 2" in output
    assert "hermes_gpt_oss120b (hermes)" in output
    assert "agent_zero_gpt_oss120b (agent-zero)" in output


def test_package_install_upgrade_ignores_subjects_attached_to_other_experiments(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from acmm.experiment_package import cli as package_cli
    from acmm_infra.cohort import cohort_subject, cohort_subject_status, experiment_cohort

    source = tmp_path / "source"
    unrelated_source = tmp_path / "unrelated-source"
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    _write_package(source)
    _write_package(unrelated_source, experiment_id="unrelated_package_v1")
    old = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    unrelated = acquire_experiment_package(
        unrelated_source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    (source / "runtime" / "__init__.py").write_text(
        _RUNTIME_INIT + "\n# upgraded\n",
        encoding="utf-8",
    )

    attached_subject = cohort_subject(
        subject_id="hermes_gpt_oss120b",
        infrastructure_subject="hermes",
    )
    references = {
        old.package_sha256: ("hermes",),
        unrelated.package_sha256: ("open-webui",),
    }
    observed: dict[str, object] = {}

    def fake_references(repo_root, observed_runtime_root, package_sha256):
        del repo_root, observed_runtime_root
        return references.get(package_sha256, ())

    def fake_attached(repo_root, observed_runtime_root, package, selected_subject_ids=()):
        del repo_root, observed_runtime_root, selected_subject_ids
        if package.package_sha256 == old.package_sha256:
            return experiment_cohort(None, None, (attached_subject,))
        raise AssertionError("upgrade inspected an unrelated experiment attachment")

    def fake_apply(repo_root, observed_runtime_root, cohort, **kwargs):
        del repo_root, observed_runtime_root, kwargs
        observed["subject_ids"] = tuple(item.subject_id for item in cohort.subjects)
        references[old.package_sha256] = ()
        return (
            cohort_subject_status(
                subject_id="hermes_gpt_oss120b",
                infrastructure_subject="hermes",
                running=False,
                experiment_id=cohort.experiment_id,
                package_state="match",
                package_sha256=cohort.package.package_sha256,
            ),
        )

    monkeypatch.setattr(package_cli, "package_deployment_references", fake_references)
    monkeypatch.setattr(package_cli, "resolve_attached_package_cohort", fake_attached)
    monkeypatch.setattr(package_cli, "apply_cohort", fake_apply)
    monkeypatch.setattr(package_cli, "apply_staged_integration_resources", lambda *a, **k: ())
    monkeypatch.setattr(
        package_cli,
        "remove_superseded_staged_integration_resources",
        lambda *a, **k: None,
    )

    parser = _build_parser()
    args = parser.parse_args(["package", "install", str(source), "--upgrade"])

    assert args.handler(args) == 0
    assert observed["subject_ids"] == ("hermes_gpt_oss120b",)
    remaining = package_cli_store_list(runtime_root)
    unrelated_after = [
        item for item in remaining if item.experiment_id == "unrelated_package_v1"
    ]
    upgraded = [item for item in remaining if item.experiment_id == EXPERIMENT_ID]
    assert len(unrelated_after) == 1
    assert unrelated_after[0].package_sha256 == unrelated.package_sha256
    assert len(upgraded) == 1
    assert "subjects moved:   1" in capsys.readouterr().out


def test_package_uninstall_requires_selector() -> None:
    parser = _build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["package", "uninstall"])


def test_package_purge_requires_explicit_detach_for_attached_revision(
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
    args = parser.parse_args(["package", "purge"])

    assert args.handler(args) == 2
    assert package.root.exists()
    assert "Detach them explicitly" in capsys.readouterr().err


def test_package_purge_detaches_and_purges_store(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from acmm.experiment_package import cli as package_cli

    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv("ACMM_RUNTIME_ROOT", str(runtime_root))
    _write_package(source)
    first = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    (source / "runtime" / "__init__.py").write_text(
        _RUNTIME_INIT + "\n# second\n",
        encoding="utf-8",
    )
    second = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    references = {
        first.package_sha256: ("hermes",),
        second.package_sha256: (),
    }

    def fake_references(repo_root, observed_runtime_root, package_sha256):
        del repo_root, observed_runtime_root
        return references[package_sha256]

    def fake_detach(package, **kwargs):
        del kwargs
        references[package.package_sha256] = ()
        return ("hermes_gpt_oss120b",)

    monkeypatch.setattr(package_cli, "package_deployment_references", fake_references)
    monkeypatch.setattr(package_cli, "_detach_installed_package", fake_detach)
    monkeypatch.setattr(package_cli, "remove_staged_integration_resources", lambda *a, **k: None)

    parser = _build_parser()
    args = parser.parse_args(
        ["package", "purge", "--detach-all"]
    )

    assert args.handler(args) == 0
    assert not (runtime_root / "_experiment_packages").exists()
    output = capsys.readouterr().out
    assert "Experiment Package store purged:" in output
    assert "revisions removed: 2" in output


def test_package_list_reports_attachment_state(
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
        lambda *args, **kwargs: ("hermes", "agent-zero"),
    )

    parser = _build_parser()
    args = parser.parse_args(["package", "list"])

    assert args.handler(args) == 0
    output = capsys.readouterr().out
    assert package.package_sha256 in output
    assert "legend: * attached to one or more subject instances" in output
    assert f"[1] * {package.experiment_id}@{package.package_sha256[:12]}" in output
    assert "attached:        hermes, agent-zero" in output
