from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping

from acmm.adapters.hermes import hermes_adapter, hermes_adapter_config
from acmm.runtime.interfaces import subject_adapter


@dataclass(frozen=True)
class subject_execution_binding:
    """Runtime adapter construction for one qualified subject."""

    adapter_builder: Callable[[], subject_adapter]
    runtime_description: str


def _build_hermes_nemotron30b_adapter() -> subject_adapter:
    return hermes_adapter(
        hermes_adapter_config(
            docker_container="acmm-hermes",
            executable="/opt/hermes/bin/hermes",
        )
    )


def _build_catalog() -> Mapping[str, subject_execution_binding]:
    bindings = {
        "hermes_nemotron30b": subject_execution_binding(
            adapter_builder=_build_hermes_nemotron30b_adapter,
            runtime_description=(
                "Hermes container: acmm-hermes; toolset overlay: acmm."
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
