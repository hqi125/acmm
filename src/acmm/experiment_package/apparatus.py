from __future__ import annotations

from acmm.experiment_package.model import (
    experiment_apparatus_component,
    experiment_package,
)
from acmm.experiment_package.snapshot import (
    staged_package_verification_error,
    verify_staged_component_snapshot,
)


class experiment_apparatus_error(ValueError):
    """Raised when staged Experiment Package apparatus cannot be resolved."""


def load_staged_experiment_apparatus(
    package: experiment_package,
    infrastructure_subject: str,
) -> experiment_apparatus_component:
    component = package.apparatus.get(infrastructure_subject)
    if component is None:
        raise experiment_apparatus_error(
            f"Experiment Package '{package.experiment_id}' does not declare apparatus "
            f"for infrastructure subject '{infrastructure_subject}'."
        )

    try:
        verify_staged_component_snapshot(
            package,
            component.root,
            component.sha256,
            f"apparatus '{infrastructure_subject}'",
        )
    except staged_package_verification_error as exc:
        raise experiment_apparatus_error(str(exc)) from exc

    return component
