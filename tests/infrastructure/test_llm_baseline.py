from pathlib import Path

import yaml

from acmm.subjects.catalog import (
    lookup_subject_execution_binding,
    registered_subject_ids,
)
from acmm_infra.deployment import write_llm_baseline_state
from acmm_infra.profiles.agent_zero import load_agent_zero_profile
from acmm_infra.profiles.hermes import load_hermes_profile
from acmm_infra.profiles.llm import (
    current_llm_baseline_id,
    load_llm_baseline,
    primary_requested_context_window,
    registered_llm_baseline_ids,
    runtime_provenance_configuration,
)
from acmm_infra.profiles.open_webui import load_open_webui_profile


ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_ID = "nemotron_3_nano_30b_cloud_v1"
CURRENT_ID = "gpt_oss_120b_cloud_v1"
CANDIDATE_ID = "gpt_oss_120b_cloud_candidate_v1"


def test_repository_registers_promoted_gpt_oss_as_current_baseline() -> None:
    assert current_llm_baseline_id(ROOT) == CURRENT_ID
    assert registered_llm_baseline_ids(ROOT) == (
        HISTORICAL_ID,
        CANDIDATE_ID,
        CURRENT_ID,
    )

    baseline = load_llm_baseline(ROOT)
    assert baseline.id == CURRENT_ID
    assert baseline.model_name == "gpt-oss:120b-cloud"
    assert baseline.logical_provider == "ollama_cloud"
    assert baseline.minimum_context_window == 64000
    assert baseline.hermes.requested_context_window == 64000
    assert baseline.open_webui.requested_context_window is None
    assert baseline.agent_zero.requested_context_window == 64000
    assert baseline.agent_zero.utility_requested_context_window == 64000


def test_repository_preserves_gpt_oss_candidate_qualification_baseline() -> None:
    candidate = load_llm_baseline(ROOT, CANDIDATE_ID)

    assert current_llm_baseline_id(ROOT) == CURRENT_ID
    assert candidate.id == CANDIDATE_ID
    assert candidate.model_name == "gpt-oss:120b-cloud"
    assert candidate.logical_provider == "ollama_cloud"
    assert candidate.minimum_context_window == 64000
    assert candidate.hermes.requested_context_window == 64000
    assert candidate.hermes.reasoning_effort == "medium"
    assert candidate.open_webui.requested_context_window is None
    assert candidate.agent_zero.requested_context_window == 64000
    assert candidate.agent_zero.utility_model_name == "gpt-oss:120b-cloud"


def test_candidate_baseline_resolves_qualification_cohort() -> None:
    from acmm_infra.cohort import resolve_baseline_cohort

    cohort = resolve_baseline_cohort(ROOT, llm_baseline_id=CANDIDATE_ID)
    assert tuple(item.subject_id for item in cohort.subjects) == (
        "hermes_llm_qualification",
        "open_webui_llm_qualification",
        "agent_zero_llm_qualification",
    )
    assert all(item.llm_baseline_id == CANDIDATE_ID for item in cohort.subjects)

    selected = resolve_baseline_cohort(
        ROOT,
        ("hermes_llm_qualification",),
        llm_baseline_id=CANDIDATE_ID,
    )
    assert len(selected.subjects) == 1
    assert selected.subjects[0].infrastructure_subject == "hermes"



def test_passive_cohort_discovers_candidate_from_deployment_state(
    tmp_path: Path,
) -> None:
    from acmm_infra.cohort import resolve_deployed_cohort

    for directory in ("hermes", "open-webui", "agent-zero"):
        write_llm_baseline_state(
            tmp_path / directory,
            llm_baseline_id=CANDIDATE_ID,
            model_name="gpt-oss:120b-cloud",
            logical_provider="ollama_cloud",
        )

    cohort = resolve_deployed_cohort(ROOT, tmp_path)

    assert tuple(item.subject_id for item in cohort.subjects) == (
        "hermes_llm_qualification",
        "open_webui_llm_qualification",
        "agent_zero_llm_qualification",
    )
    assert all(item.llm_baseline_id == CANDIDATE_ID for item in cohort.subjects)

def test_runtime_context_metadata_is_agent_specific_and_baseline_driven() -> None:
    baseline = load_llm_baseline(ROOT, HISTORICAL_ID)

    assert primary_requested_context_window(baseline, "hermes") == 64000
    assert primary_requested_context_window(baseline, "open-webui") is None
    assert primary_requested_context_window(baseline, "agent-zero") == 64000

    open_webui = runtime_provenance_configuration(baseline, "open-webui")
    assert open_webui == {
        "llm_baseline_id": HISTORICAL_ID,
        "llm_minimum_context_window": 64000,
        "llm_requested_context_window": None,
    }
    agent_zero = runtime_provenance_configuration(baseline, "agent-zero")
    assert agent_zero["llm_utility_model"] == "nemotron-3-nano:30b-cloud"
    assert agent_zero["llm_utility_requested_context_window"] == 64000



def test_agent_profiles_compose_model_settings_from_llm_baseline() -> None:
    hermes = load_hermes_profile(ROOT, HISTORICAL_ID)
    open_webui = load_open_webui_profile(ROOT, HISTORICAL_ID)
    agent_zero = load_agent_zero_profile(ROOT, HISTORICAL_ID)

    for profile in (hermes, open_webui, agent_zero):
        assert profile.llm_baseline_id == HISTORICAL_ID
        assert profile.llm_logical_provider == "ollama_cloud"
        assert profile.llm_minimum_context_window == 64000
        assert profile.model_default == "nemotron-3-nano:30b-cloud"

    assert hermes.context_window == 64000
    assert open_webui.llm_requested_context_window is None
    assert agent_zero.context_window == 64000
    assert agent_zero.utility_model_default == "nemotron-3-nano:30b-cloud"
    assert agent_zero.utility_context_window == 64000


def test_subject_catalog_binds_historical_subjects_to_llm_baseline() -> None:
    expected = (
        "hermes_nemotron30b",
        "open_webui_nemotron30b",
        "agent_zero_nemotron30b",
    )
    assert registered_subject_ids(HISTORICAL_ID) == expected
    for subject_id in expected:
        binding = lookup_subject_execution_binding(subject_id)
        assert binding is not None
        assert binding.llm_baseline_id == HISTORICAL_ID


def test_promoted_gpt_oss_baseline_has_permanent_scientific_subjects() -> None:
    expected = (
        "hermes_gpt_oss120b",
        "open_webui_gpt_oss120b",
        "agent_zero_gpt_oss120b",
    )
    assert registered_subject_ids(CURRENT_ID) == expected
    for subject_id in expected:
        binding = lookup_subject_execution_binding(subject_id)
        assert binding is not None
        assert binding.llm_baseline_id == CURRENT_ID


def test_agent_profile_yaml_no_longer_owns_model_identity() -> None:
    forbidden = {
        "model_default",
        "context_window",
        "model_provider",
        "model_base_url",
        "utility_model_default",
        "utility_context_window",
    }
    for relative in (
        "infrastructure/hermes/profile.yaml",
        "infrastructure/open-webui/profile.yaml",
        "infrastructure/agent-zero/profile.yaml",
    ):
        data = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
        baseline = data["baseline"]
        assert forbidden.isdisjoint(baseline)


def test_experiment_manager_resolution_uses_historical_subject_baseline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import acmm_infra.subject_managers as managers

    captured: dict[str, str | None] = {}

    class fake_manager:
        def __init__(self, baseline_id: str) -> None:
            self.profile = type(
                "profile",
                (),
                {"llm_baseline_id": baseline_id},
            )()

    def fake_builder(repo, runtime, experiment_id, llm_baseline_id):
        del repo, runtime
        captured["experiment_id"] = experiment_id
        captured["llm_baseline_id"] = llm_baseline_id
        assert llm_baseline_id is not None
        return fake_manager(llm_baseline_id)

    monkeypatch.setitem(managers._MANAGER_BUILDERS, "hermes", fake_builder)
    context = managers.build_subject_manager(
        ROOT,
        tmp_path / "runtime",
        "hermes",
        "controlled_authoritative_state_interference_v1",
    )

    assert captured == {
        "experiment_id": "controlled_authoritative_state_interference_v1",
        "llm_baseline_id": HISTORICAL_ID,
    }
    assert context.llm_baseline_id == HISTORICAL_ID
