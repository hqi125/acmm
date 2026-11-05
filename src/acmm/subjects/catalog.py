from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from acmm.adapters.agent_zero import agent_zero_adapter
from acmm.adapters.agent_zero_api import agent_zero_runtime_api_client
from acmm.adapters.hermes import hermes_adapter, hermes_adapter_config
from acmm.adapters.open_webui import open_webui_adapter
from acmm.adapters.open_webui_api import open_webui_runtime_api_client
from acmm.runtime.interfaces import subject_adapter


@dataclass(frozen=True)
class subject_termination_support:
    """Termination controls a qualified subject can enforce for one run."""

    max_steps: bool
    timeout_seconds: bool


@dataclass(frozen=True)
class subject_execution_binding:
    """Runtime adapter construction for one qualified subject."""

    adapter_builder: Callable[[], subject_adapter]
    runtime_details: tuple[tuple[str, str], ...]
    infrastructure_subject: str
    llm_baseline_id: str
    termination_support: subject_termination_support


def _build_hermes_adapter() -> subject_adapter:
    return hermes_adapter(
        hermes_adapter_config(
            docker_container="acmm-hermes",
            executable="/opt/hermes/bin/hermes",
        )
    )


def _build_open_webui_adapter() -> subject_adapter:
    return open_webui_adapter(open_webui_runtime_api_client())


def _build_agent_zero_adapter() -> subject_adapter:
    return agent_zero_adapter(agent_zero_runtime_api_client())


def _build_catalog() -> Mapping[str, subject_execution_binding]:
    bindings = {
        "hermes_nemotron30b": subject_execution_binding(
            adapter_builder=_build_hermes_adapter,
            runtime_details=(
                ("container", "acmm-hermes"),
                ("experiment integrations", "package-managed"),
            ),
            infrastructure_subject="hermes",
            llm_baseline_id="nemotron_3_nano_30b_cloud_v1",
            termination_support=subject_termination_support(
                max_steps=True,
                timeout_seconds=True,
            ),
        ),
        "open_webui_nemotron30b": subject_execution_binding(
            adapter_builder=_build_open_webui_adapter,
            runtime_details=(
                ("API", "http://127.0.0.1:3000"),
                ("experiment integrations", "package-managed"),
            ),
            infrastructure_subject="open-webui",
            llm_baseline_id="nemotron_3_nano_30b_cloud_v1",
            termination_support=subject_termination_support(
                max_steps=False,
                timeout_seconds=True,
            ),
        ),
        "agent_zero_nemotron30b": subject_execution_binding(
            adapter_builder=_build_agent_zero_adapter,
            runtime_details=(
                ("API", "http://127.0.0.1:50080"),
                ("profile", "acmm"),
                ("experiment integrations", "package-managed"),
            ),
            infrastructure_subject="agent-zero",
            llm_baseline_id="nemotron_3_nano_30b_cloud_v1",
            termination_support=subject_termination_support(
                max_steps=False,
                timeout_seconds=True,
            ),
        ),
        "hermes_gpt_oss120b": subject_execution_binding(
            adapter_builder=_build_hermes_adapter,
            runtime_details=(
                ("container", "acmm-hermes"),
                ("experiment integrations", "package-managed"),
            ),
            infrastructure_subject="hermes",
            llm_baseline_id="gpt_oss_120b_cloud_v1",
            termination_support=subject_termination_support(
                max_steps=True,
                timeout_seconds=True,
            ),
        ),
        "open_webui_gpt_oss120b": subject_execution_binding(
            adapter_builder=_build_open_webui_adapter,
            runtime_details=(
                ("API", "http://127.0.0.1:3000"),
                ("experiment integrations", "package-managed"),
            ),
            infrastructure_subject="open-webui",
            llm_baseline_id="gpt_oss_120b_cloud_v1",
            termination_support=subject_termination_support(
                max_steps=False,
                timeout_seconds=True,
            ),
        ),
        "agent_zero_gpt_oss120b": subject_execution_binding(
            adapter_builder=_build_agent_zero_adapter,
            runtime_details=(
                ("API", "http://127.0.0.1:50080"),
                ("profile", "acmm"),
                ("experiment integrations", "package-managed"),
            ),
            infrastructure_subject="agent-zero",
            llm_baseline_id="gpt_oss_120b_cloud_v1",
            termination_support=subject_termination_support(
                max_steps=False,
                timeout_seconds=True,
            ),
        ),
    }

    return MappingProxyType(bindings)


_SUBJECT_CATALOG = _build_catalog()


def lookup_subject_execution_binding(
    subject_id: str,
) -> subject_execution_binding | None:
    """Return the qualified runtime binding for a subject, if registered."""

    return _SUBJECT_CATALOG.get(subject_id)


def registered_subject_ids(
    llm_baseline_id: str | None = None,
) -> tuple[str, ...]:
    """Return qualified subject IDs in catalog declaration order."""

    if llm_baseline_id is None:
        return tuple(_SUBJECT_CATALOG)
    return tuple(
        subject_id
        for subject_id, binding in _SUBJECT_CATALOG.items()
        if binding.llm_baseline_id == llm_baseline_id
    )


def unsupported_termination_controls(
    binding: subject_execution_binding,
    termination: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return requested termination controls the subject cannot enforce exactly."""

    unsupported: list[str] = []

    if "max_steps" in termination and not binding.termination_support.max_steps:
        unsupported.append("max_steps")

    if (
        "timeout_seconds" in termination
        and not binding.termination_support.timeout_seconds
    ):
        unsupported.append("timeout_seconds")

    return tuple(unsupported)
