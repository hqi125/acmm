from __future__ import annotations

from dataclasses import dataclass

from acmm.experiment_package.model import experiment_package
from acmm.experiment_package.snapshot import (
    staged_package_verification_error,
    verify_staged_component_snapshot,
    verify_staged_package_snapshot,
)


@dataclass(frozen=True)
class experiment_package_provenance:
    experiment_id: str
    package_schema_version: str
    package_sha256: str
    contract_sha256: str
    runtime_sha256: str | None


@dataclass(frozen=True)
class experiment_integration_provenance:
    integration_id: str
    sha256: str


@dataclass(frozen=True)
class experiment_deployment_provenance:
    package: experiment_package_provenance
    infrastructure_subject: str
    integrations: tuple[experiment_integration_provenance, ...]


class experiment_provenance_error(ValueError):
    """Raised when Experiment Package provenance cannot be trusted."""


def package_provenance(
    package: experiment_package,
) -> experiment_package_provenance:
    try:
        verify_staged_package_snapshot(package)
    except staged_package_verification_error as exc:
        raise experiment_provenance_error(str(exc)) from exc

    return experiment_package_provenance(
        experiment_id=package.experiment_id,
        package_schema_version=package.schema_version,
        package_sha256=package.package_sha256,
        contract_sha256=package.contract_sha256,
        runtime_sha256=(package.runtime.sha256 if package.runtime is not None else None),
    )


def deployment_provenance(
    package: experiment_package,
    infrastructure_subject: str,
) -> experiment_deployment_provenance:
    package_identity = package_provenance(package)
    integrations: list[experiment_integration_provenance] = []

    for component in package.integrations:
        try:
            verify_staged_component_snapshot(
                package,
                component.root,
                component.sha256,
                f"integration '{component.integration_id}'",
            )
        except staged_package_verification_error as exc:
            raise experiment_provenance_error(str(exc)) from exc
        integrations.append(
            experiment_integration_provenance(
                integration_id=component.integration_id,
                sha256=component.sha256,
            )
        )

    return experiment_deployment_provenance(
        package=package_identity,
        infrastructure_subject=infrastructure_subject,
        integrations=tuple(integrations),
    )
