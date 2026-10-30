from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from acmm_infra.profiles.common import _nonempty_string, profile_error


@dataclass(frozen=True)
class hermes_llm_settings:
    model_provider: str
    model_base_url: str
    model_api_mode: str
    requested_context_window: int
    reasoning_effort: str


@dataclass(frozen=True)
class open_webui_llm_settings:
    ollama_base_url: str
    requested_context_window: int | None


@dataclass(frozen=True)
class agent_zero_llm_settings:
    model_provider: str
    model_base_url: str
    requested_context_window: int
    utility_model_provider: str
    utility_model_base_url: str
    utility_model_name: str
    utility_requested_context_window: int


@dataclass(frozen=True)
class llm_baseline:
    id: str
    model_name: str
    logical_provider: str
    minimum_context_window: int
    hermes: hermes_llm_settings
    open_webui: open_webui_llm_settings
    agent_zero: agent_zero_llm_settings


def current_llm_baseline_id(repo_root: str | Path) -> str:
    data = _load_catalog(repo_root)
    return _nonempty_string(data.get("current"), "current")


def registered_llm_baseline_ids(repo_root: str | Path) -> tuple[str, ...]:
    data = _load_catalog(repo_root)
    baselines = data.get("baselines")
    if not isinstance(baselines, dict):
        raise profile_error("LLM baseline catalog baselines must be a mapping.")
    return tuple(baselines)


def load_llm_baseline(
    repo_root: str | Path,
    baseline_id: str | None = None,
) -> llm_baseline:
    root = Path(repo_root).expanduser().resolve()
    catalog = _load_catalog(root)
    selected = baseline_id or _nonempty_string(catalog.get("current"), "current")
    baselines = catalog.get("baselines")
    if not isinstance(baselines, dict):
        raise profile_error("LLM baseline catalog baselines must be a mapping.")

    entry = baselines.get(selected)
    if not isinstance(entry, dict):
        raise profile_error(f"Unknown LLM baseline '{selected}'.")
    definition = _nonempty_string(
        entry.get("definition"),
        f"baselines.{selected}.definition",
    )
    definition_path = root / "infrastructure" / "llm-baselines" / definition
    data = _load_yaml(definition_path, f"LLM baseline '{selected}'")
    if data.get("schema_version") != "0.1":
        raise profile_error(
            f"LLM baseline '{selected}' schema_version must be '0.1'."
        )

    baseline_data = data.get("baseline")
    agents = data.get("agents")
    if not isinstance(baseline_data, dict):
        raise profile_error(f"LLM baseline '{selected}' baseline must be a mapping.")
    if not isinstance(agents, dict):
        raise profile_error(f"LLM baseline '{selected}' agents must be a mapping.")

    declared_id = _nonempty_string(baseline_data.get("id"), "baseline.id")
    if declared_id != selected:
        raise profile_error(
            f"LLM baseline definition declares id '{declared_id}', expected '{selected}'."
        )

    minimum_context_window = _positive_int(
        baseline_data.get("minimum_context_window"),
        "baseline.minimum_context_window",
    )
    hermes_data = _mapping(agents.get("hermes"), "agents.hermes")
    open_webui_data = _mapping(agents.get("open_webui"), "agents.open_webui")
    agent_zero_data = _mapping(agents.get("agent_zero"), "agents.agent_zero")

    hermes_context = _positive_int(
        hermes_data.get("requested_context_window"),
        "agents.hermes.requested_context_window",
    )
    open_webui_context = _optional_positive_int(
        open_webui_data.get("requested_context_window"),
        "agents.open_webui.requested_context_window",
    )
    agent_zero_context = _positive_int(
        agent_zero_data.get("requested_context_window"),
        "agents.agent_zero.requested_context_window",
    )
    utility_context = _positive_int(
        agent_zero_data.get("utility_requested_context_window"),
        "agents.agent_zero.utility_requested_context_window",
    )
    for field_name, value in (
        ("agents.hermes.requested_context_window", hermes_context),
        ("agents.open_webui.requested_context_window", open_webui_context),
        ("agents.agent_zero.requested_context_window", agent_zero_context),
        ("agents.agent_zero.utility_requested_context_window", utility_context),
    ):
        if value is not None and value < minimum_context_window:
            raise profile_error(
                f"{field_name} must be at least the baseline minimum context window "
                f"of {minimum_context_window}."
            )

    return llm_baseline(
        id=declared_id,
        model_name=_nonempty_string(
            baseline_data.get("model_name"),
            "baseline.model_name",
        ),
        logical_provider=_nonempty_string(
            baseline_data.get("logical_provider"),
            "baseline.logical_provider",
        ),
        minimum_context_window=minimum_context_window,
        hermes=hermes_llm_settings(
            model_provider=_nonempty_string(
                hermes_data.get("model_provider"),
                "agents.hermes.model_provider",
            ),
            model_base_url=_nonempty_string(
                hermes_data.get("model_base_url"),
                "agents.hermes.model_base_url",
            ),
            model_api_mode=_nonempty_string(
                hermes_data.get("model_api_mode"),
                "agents.hermes.model_api_mode",
            ),
            requested_context_window=hermes_context,
            reasoning_effort=_nonempty_string(
                hermes_data.get("reasoning_effort"),
                "agents.hermes.reasoning_effort",
            ),
        ),
        open_webui=open_webui_llm_settings(
            ollama_base_url=_nonempty_string(
                open_webui_data.get("ollama_base_url"),
                "agents.open_webui.ollama_base_url",
            ),
            requested_context_window=open_webui_context,
        ),
        agent_zero=agent_zero_llm_settings(
            model_provider=_nonempty_string(
                agent_zero_data.get("model_provider"),
                "agents.agent_zero.model_provider",
            ),
            model_base_url=_nonempty_string(
                agent_zero_data.get("model_base_url"),
                "agents.agent_zero.model_base_url",
            ),
            requested_context_window=agent_zero_context,
            utility_model_provider=_nonempty_string(
                agent_zero_data.get("utility_model_provider"),
                "agents.agent_zero.utility_model_provider",
            ),
            utility_model_base_url=_nonempty_string(
                agent_zero_data.get("utility_model_base_url"),
                "agents.agent_zero.utility_model_base_url",
            ),
            utility_model_name=_nonempty_string(
                agent_zero_data.get("utility_model_name"),
                "agents.agent_zero.utility_model_name",
            ),
            utility_requested_context_window=utility_context,
        ),
    )


def primary_requested_context_window(
    baseline: llm_baseline,
    infrastructure_subject: str,
) -> int | None:
    if infrastructure_subject == "hermes":
        return baseline.hermes.requested_context_window
    if infrastructure_subject == "open-webui":
        return baseline.open_webui.requested_context_window
    if infrastructure_subject == "agent-zero":
        return baseline.agent_zero.requested_context_window
    raise profile_error(
        f"Unknown infrastructure subject '{infrastructure_subject}' for LLM baseline."
    )


def runtime_provenance_configuration(
    baseline: llm_baseline,
    infrastructure_subject: str,
) -> dict[str, Any]:
    requested = primary_requested_context_window(
        baseline,
        infrastructure_subject,
    )
    configuration: dict[str, Any] = {
        "llm_baseline_id": baseline.id,
        "llm_minimum_context_window": baseline.minimum_context_window,
        "llm_requested_context_window": requested,
    }
    if infrastructure_subject == "agent-zero":
        configuration.update(
            {
                "llm_utility_model": baseline.agent_zero.utility_model_name,
                "llm_utility_requested_context_window": (
                    baseline.agent_zero.utility_requested_context_window
                ),
            }
        )
    return configuration


def _load_catalog(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    path = root / "infrastructure" / "llm-baselines" / "catalog.yaml"
    data = _load_yaml(path, "LLM baseline catalog")
    if data.get("schema_version") != "0.1":
        raise profile_error("LLM baseline catalog schema_version must be '0.1'.")
    return data


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as exc:
        raise profile_error(f"Could not read {label} '{path}': {exc}") from exc
    except yaml.YAMLError as exc:
        raise profile_error(f"Could not parse {label} '{path}': {exc}") from exc
    if not isinstance(data, dict):
        raise profile_error(f"{label} must be a YAML mapping.")
    return data


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise profile_error(f"{field_name} must be a mapping.")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise profile_error(f"{field_name} must be a positive integer.")
    return value


def _optional_positive_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, field_name)
