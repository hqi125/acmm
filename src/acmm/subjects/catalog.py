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
    termination_support: subject_termination_support


def _build_hermes_nemotron30b_adapter() -> subject_adapter:
    return hermes_adapter(
        hermes_adapter_config(
            docker_container="acmm-hermes",
            executable="/opt/hermes/bin/hermes",
        )
    )


def _build_open_webui_nemotron30b_adapter() -> subject_adapter:
    return open_webui_adapter(open_webui_runtime_api_client())


def _build_agent_zero_nemotron30b_adapter() -> subject_adapter:
    return agent_zero_adapter(agent_zero_runtime_api_client())


def _build_catalog() -> Mapping[str, subject_execution_binding]:
    bindings = {
        "hermes_nemotron30b": subject_execution_binding(
            adapter_builder=_build_hermes_nemotron30b_adapter,
            runtime_details=(
                ("container", "acmm-hermes"),
                ("toolset overlay", "acmm"),
            ),
            infrastructure_subject="hermes",
            termination_support=subject_termination_support(
                max_steps=True,
                timeout_seconds=True,
            ),
        ),
        "open_webui_nemotron30b": subject_execution_binding(
            adapter_builder=_build_open_webui_nemotron30b_adapter,
            runtime_details=(
                ("API", "http://127.0.0.1:3000"),
                ("experiment Tool apparatus", "selected separately"),
            ),
            infrastructure_subject="open-webui",
            termination_support=subject_termination_support(
                max_steps=False,
                timeout_seconds=True,
            ),
        ),
        "agent_zero_nemotron30b": subject_execution_binding(
            adapter_builder=_build_agent_zero_nemotron30b_adapter,
            runtime_details=(
                ("API", "http://127.0.0.1:50080"),
                ("profile", "acmm"),
                ("experiment plugin apparatus", "selected separately"),
            ),
            infrastructure_subject="agent-zero",
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


def registered_subject_ids() -> tuple[str, ...]:
    """Return qualified subject IDs in catalog declaration order."""

    return tuple(_SUBJECT_CATALOG)


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
