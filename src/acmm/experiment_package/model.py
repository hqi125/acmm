from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from acmm.validation import validation_issue


@dataclass(frozen=True)
class experiment_runtime_component:
    api_version: str
    root: Path
    entrypoint: str
    sha256: str


@dataclass(frozen=True)
class experiment_apparatus_component:
    infrastructure_subject: str
    root: Path
    sha256: str


@dataclass(frozen=True)
class experiment_integration_component:
    integration_id: str
    api_version: str
    root: Path
    entrypoint: str
    preset: str | None
    configuration: Mapping[str, Any]
    sha256: str


@dataclass(frozen=True)
class experiment_analysis_component:
    kind: str
    configuration: Path
    sha256: str


@dataclass(frozen=True)
class experiment_package:
    root: Path
    schema_version: str
    experiment_id: str
    contract_path: Path
    contract: Mapping[str, Any]
    contract_sha256: str
    package_sha256: str
    runtime: experiment_runtime_component | None
    apparatus: Mapping[str, experiment_apparatus_component]
    integrations: tuple[experiment_integration_component, ...]
    analysis: tuple[experiment_analysis_component, ...]
    contract_warnings: tuple[validation_issue, ...]
