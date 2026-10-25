from pathlib import Path

from acmm.deployment_preflight import inspect_deployment_contexts
from acmm.subjects.catalog import lookup_subject_execution_binding
from acmm_infra.deployment import write_experiment_apparatus_state
from acmm_infra.profiles.experiments import experiment_apparatus_sha256


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "controlled_tool_use_multiscenario_v1"


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
