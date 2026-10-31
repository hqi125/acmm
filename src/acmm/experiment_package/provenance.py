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
class experiment_deployment_provenance:
    package: experiment_package_provenance
    infrastructure_subject: str
    apparatus_sha256: str | None


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
        runtime_sha256=(
            package.runtime.sha256
            if package.runtime is not None
            else None
        ),
    )


def deployment_provenance(
    package: experiment_package,
    infrastructure_subject: str,
) -> experiment_deployment_provenance:
    package_identity = package_provenance(package)
    component = package.apparatus.get(infrastructure_subject)
    if component is None:
        return experiment_deployment_provenance(
            package=package_identity,
            infrastructure_subject=infrastructure_subject,
            apparatus_sha256=None,
        )

    try:
        verify_staged_component_snapshot(
            package,
            component.root,
            component.sha256,
            f"apparatus '{infrastructure_subject}'",
        )
    except staged_package_verification_error as exc:
        raise experiment_provenance_error(str(exc)) from exc

    return experiment_deployment_provenance(
        package=package_identity,
        infrastructure_subject=infrastructure_subject,
        apparatus_sha256=component.sha256,
    )


def apparatus_provenance(
    package: experiment_package,
    infrastructure_subject: str,
) -> experiment_deployment_provenance:
    provenance = deployment_provenance(package, infrastructure_subject)
    if provenance.apparatus_sha256 is None:
        raise experiment_provenance_error(
            f"Experiment Package '{package.experiment_id}' does not declare apparatus "
            f"for infrastructure subject '{infrastructure_subject}'."
        )
    return provenance
