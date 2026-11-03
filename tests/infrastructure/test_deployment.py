from pathlib import Path

import pytest

from acmm.experiment_package.provenance import (
    experiment_deployment_provenance,
    experiment_package_provenance,
)
from acmm_infra.common import infrastructure_error
from acmm_infra.deployment import (
    bool_text,
    files_equal,
    install_default_if_missing,
    read_env,
    read_experiment_apparatus_state,
    read_llm_baseline_state,
    write_experiment_apparatus_state,
    write_llm_baseline_state,
    write_env,
)


def test_install_default_if_missing_creates_then_preserves(tmp_path: Path) -> None:
    source = tmp_path / "default.txt"
    destination = tmp_path / "runtime" / "default.txt"
    source.write_text("default\n", encoding="utf-8")

    assert install_default_if_missing(source, destination) == "created"
    assert destination.read_text(encoding="utf-8") == "default\n"

    destination.write_text("custom\n", encoding="utf-8")
    assert install_default_if_missing(source, destination) == "preserved"
    assert destination.read_text(encoding="utf-8") == "custom\n"


def test_install_default_if_missing_requires_source(tmp_path: Path) -> None:
    with pytest.raises(infrastructure_error):
        install_default_if_missing(
            tmp_path / "missing.txt",
            tmp_path / "runtime.txt",
        )


def test_read_env_ignores_comments_blank_lines_and_invalid_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n\nA=1\ninvalid\nB = two words\nC=value=with=equals\n",
        encoding="utf-8",
    )

    assert read_env(path) == {
        "A": "1",
        "B": "two words",
        "C": "value=with=equals",
    }
    assert read_env(tmp_path / "missing.env") == {}


def test_write_env_sorts_keys_and_honors_line_ending(tmp_path: Path) -> None:
    path = tmp_path / ".env"

    write_env(
        path,
        {"B": "2", "A": "1"},
        header_lines=("# first", "# second"),
        line_ending="\r\n",
    )

    assert path.read_bytes() == (
        b"# first\r\n"
        b"# second\r\n"
        b"\r\n"
        b"A=1\r\n"
        b"B=2\r\n"
    )


def test_files_equal_requires_equal_regular_files(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.write_bytes(b"same")
    right.write_bytes(b"same")

    assert files_equal(left, right) is True

    right.write_bytes(b"different")
    assert files_equal(left, right) is False
    assert files_equal(left, tmp_path / "missing") is False


def test_bool_text_uses_lowercase_boolean_literals() -> None:
    assert bool_text(True) == "true"
    assert bool_text(False) == "false"



def test_llm_baseline_state_records_deployed_baseline(tmp_path: Path) -> None:
    write_llm_baseline_state(
        tmp_path,
        llm_baseline_id="gpt_oss_120b_cloud_candidate_v1",
        model_name="gpt-oss:120b-cloud",
        logical_provider="ollama_cloud",
    )

    state = read_llm_baseline_state(tmp_path)

    assert state is not None
    assert state["schema_version"] == "0.1"
    assert state["llm_baseline_id"] == "gpt_oss_120b_cloud_candidate_v1"
    assert state["model_name"] == "gpt-oss:120b-cloud"
    assert state["logical_provider"] == "ollama_cloud"

def test_experiment_apparatus_state_records_application_scope(
    tmp_path: Path,
) -> None:
    write_experiment_apparatus_state(
        tmp_path,
        experiment_id="package_fixture_v1",
        source_sha256="abc123",
        application_scope="tools",
        resources=[{"kind": "tool", "id": "acmm_tools"}],
    )

    state = read_experiment_apparatus_state(tmp_path)

    assert state is not None
    assert state["schema_version"] == "0.3"
    assert state["application_scope"] == "tools"
    assert state["experiment_id"] == "package_fixture_v1"


def test_experiment_apparatus_state_reads_legacy_scope_as_full_experiment(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".acmm-experiment-apparatus.json"
    path.write_text(
        "{\n"
        '  "schema_version": "0.1",\n'
        '  "experiment_id": "package_fixture_v1",\n'
        '  "source_sha256": "abc123",\n'
        '  "resources": []\n'
        "}\n",
        encoding="utf-8",
    )

    state = read_experiment_apparatus_state(tmp_path)

    assert state is not None
    assert state["application_scope"] == "experiment"


def test_experiment_apparatus_state_records_package_provenance(
    tmp_path: Path,
) -> None:
    package_sha256 = "a" * 64
    contract_sha256 = "b" * 64
    runtime_sha256 = "c" * 64
    apparatus_sha256 = "d" * 64
    provenance = experiment_deployment_provenance(
        package=experiment_package_provenance(
            experiment_id="example_v1",
            package_schema_version="0.1",
            package_sha256=package_sha256,
            contract_sha256=contract_sha256,
            runtime_sha256=runtime_sha256,
        ),
        infrastructure_subject="hermes",
        apparatus_sha256=apparatus_sha256,
    )

    write_experiment_apparatus_state(
        tmp_path,
        experiment_id="example_v1",
        source_sha256=apparatus_sha256,
        application_scope="experiment",
        resources=[],
        package_provenance=provenance,
    )

    state = read_experiment_apparatus_state(tmp_path)
    assert state is not None
    assert state["schema_version"] == "0.3"
    assert state["source_sha256"] == apparatus_sha256
    assert state["package"] == {
        "schema_version": "0.1",
        "sha256": package_sha256,
        "contract_sha256": contract_sha256,
        "runtime_sha256": runtime_sha256,
        "apparatus_subject": "hermes",
        "apparatus_sha256": apparatus_sha256,
    }


def test_experiment_apparatus_state_reads_v02_without_package_provenance(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".acmm-experiment-apparatus.json"
    path.write_text(
        "{\n"
        '  "schema_version": "0.2",\n'
        '  "experiment_id": "example_v1",\n'
        '  "source_sha256": "legacy",\n'
        '  "application_scope": "experiment",\n'
        '  "resources": []\n'
        "}\n",
        encoding="utf-8",
    )

    state = read_experiment_apparatus_state(tmp_path)

    assert state is not None
    assert state["schema_version"] == "0.2"
    assert state["package"] is None


def test_experiment_apparatus_state_rejects_mismatched_package_digest(
    tmp_path: Path,
) -> None:
    provenance = experiment_deployment_provenance(
        package=experiment_package_provenance(
            experiment_id="example_v1",
            package_schema_version="0.1",
            package_sha256="a" * 64,
            contract_sha256="b" * 64,
            runtime_sha256=None,
        ),
        infrastructure_subject="hermes",
        apparatus_sha256="c" * 64,
    )

    with pytest.raises(infrastructure_error, match="does not match source_sha256"):
        write_experiment_apparatus_state(
            tmp_path,
            experiment_id="example_v1",
            source_sha256="d" * 64,
            application_scope="experiment",
            resources=[],
            package_provenance=provenance,
        )
