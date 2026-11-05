from pathlib import Path

import pytest

from acmm.experiment_package.provenance import (
    experiment_deployment_provenance,
    experiment_integration_provenance,
    experiment_package_provenance,
)
from acmm_infra.common import infrastructure_error
from acmm_infra.deployment import (
    bool_text,
    clear_experiment_package_state,
    experiment_package_state_matches,
    files_equal,
    install_default_if_missing,
    read_env,
    read_experiment_package_state,
    read_llm_baseline_state,
    write_env,
    write_experiment_package_state,
    write_llm_baseline_state,
)


def _provenance() -> experiment_deployment_provenance:
    return experiment_deployment_provenance(
        package=experiment_package_provenance(
            experiment_id="example_v1",
            package_schema_version="0.2",
            package_sha256="a" * 64,
            contract_sha256="b" * 64,
            runtime_sha256="c" * 64,
        ),
        infrastructure_subject="hermes",
        integrations=(
            experiment_integration_provenance(
                integration_id="example-capability",
                sha256="d" * 64,
            ),
        ),
    )


def test_install_default_if_missing_creates_then_preserves(tmp_path: Path) -> None:
    source = tmp_path / "default.txt"
    destination = tmp_path / "runtime" / "default.txt"
    source.write_text("default\n", encoding="utf-8")
    assert install_default_if_missing(source, destination) == "created"
    destination.write_text("custom\n", encoding="utf-8")
    assert install_default_if_missing(source, destination) == "preserved"
    assert destination.read_text(encoding="utf-8") == "custom\n"


def test_install_default_if_missing_requires_source(tmp_path: Path) -> None:
    with pytest.raises(infrastructure_error):
        install_default_if_missing(tmp_path / "missing.txt", tmp_path / "runtime.txt")


def test_read_env_ignores_comments_blank_lines_and_invalid_lines(tmp_path: Path) -> None:
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
        b"# first\r\n# second\r\n\r\nA=1\r\nB=2\r\n"
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
    assert state["llm_baseline_id"] == "gpt_oss_120b_cloud_candidate_v1"
    assert state["model_name"] == "gpt-oss:120b-cloud"


def test_experiment_package_state_records_provenance_and_resources(
    tmp_path: Path,
) -> None:
    provenance = _provenance()
    write_experiment_package_state(
        tmp_path,
        experiment_id="example_v1",
        resources=[{"kind": "managed_config", "key": "example.key"}],
        package_provenance=provenance,
    )
    state = read_experiment_package_state(tmp_path)
    assert state is not None
    assert state["experiment_id"] == "example_v1"
    assert state["resources"] == [
        {"kind": "managed_config", "key": "example.key"}
    ]
    assert state["package"] == {
        "schema_version": "0.2",
        "sha256": "a" * 64,
        "contract_sha256": "b" * 64,
        "runtime_sha256": "c" * 64,
        "subject": "hermes",
        "integrations": [
            {"id": "example-capability", "sha256": "d" * 64}
        ],
    }


def test_experiment_package_state_rejects_mismatched_experiment_id(
    tmp_path: Path,
) -> None:
    with pytest.raises(infrastructure_error, match="experiment ID"):
        write_experiment_package_state(
            tmp_path,
            experiment_id="other_v1",
            resources=[],
            package_provenance=_provenance(),
        )


def test_experiment_package_state_matching_is_exact(tmp_path: Path) -> None:
    provenance = _provenance()
    write_experiment_package_state(
        tmp_path,
        experiment_id="example_v1",
        resources=[],
        package_provenance=provenance,
    )
    state = read_experiment_package_state(tmp_path)
    assert experiment_package_state_matches(
        state,
        experiment_id="example_v1",
        provenance=provenance,
    )[0]

    assert not experiment_package_state_matches(
        state,
        experiment_id="wrong_v1",
        provenance=provenance,
    )[0]


def test_clear_experiment_package_state_restores_baseline_state(tmp_path: Path) -> None:
    write_experiment_package_state(
        tmp_path,
        experiment_id="example_v1",
        resources=[],
        package_provenance=_provenance(),
    )
    clear_experiment_package_state(tmp_path)
    assert read_experiment_package_state(tmp_path) is None
