from __future__ import annotations

from pathlib import Path

from acmm.experiment_package.model import experiment_package
from acmm.experiment_package.source_tree import (
    component_tree_sha256,
    package_tree_sha256,
)


_STAGED_PACKAGE_DIRECTORY = "_experiment_packages"


class staged_package_verification_error(ValueError):
    """Raised when an Experiment Package staged snapshot is not trustworthy."""


def verify_staged_package_snapshot(package: experiment_package) -> None:
    root = package.root
    if (
        root.name != package.package_sha256
        or root.parent.name != package.experiment_id
        or root.parent.parent.name != _STAGED_PACKAGE_DIRECTORY
    ):
        raise staged_package_verification_error(
            "Experiment Package is not an ACMM-staged package snapshot."
        )

    observed = package_tree_sha256(root)
    if observed != package.package_sha256:
        raise staged_package_verification_error(
            "Staged Experiment Package changed after acquisition: "
            f"expected {package.package_sha256}, observed {observed}."
        )


def verify_staged_component_snapshot(
    package: experiment_package,
    component_root: Path,
    expected_sha256: str,
    description: str,
) -> None:
    verify_staged_package_snapshot(package)
    observed = component_tree_sha256(component_root)
    if observed != expected_sha256:
        raise staged_package_verification_error(
            f"Staged Experiment Package {description} changed after acquisition: "
            f"expected {expected_sha256}, observed {observed}."
        )
