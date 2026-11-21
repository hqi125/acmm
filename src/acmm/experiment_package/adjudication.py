from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

from acmm.adjudication_api import (
    EXPERIMENT_ADJUDICATION_API_VERSION,
    experiment_adjudication_context,
    experiment_adjudicator,
)
from acmm.experiment_package.model import experiment_package
from acmm.experiment_package.snapshot import (
    staged_package_verification_error,
    verify_staged_component_snapshot,
)


class experiment_adjudication_error(ValueError):
    """Raised when staged Experiment Package adjudication code cannot be activated."""


@dataclass(frozen=True)
class loaded_experiment_adjudicator:
    context: experiment_adjudication_context
    adjudicator: experiment_adjudicator
    module_name: str


def load_staged_experiment_adjudicator(
    package: experiment_package,
) -> loaded_experiment_adjudicator:
    component = package.adjudication
    if component is None:
        raise experiment_adjudication_error(
            f"Experiment Package '{package.experiment_id}' does not declare adjudication code."
        )

    _verify_adjudication_snapshot(package)
    if component.api_version != EXPERIMENT_ADJUDICATION_API_VERSION:
        raise experiment_adjudication_error(
            f"Unsupported adjudication API version '{component.api_version}'."
        )

    context = experiment_adjudication_context(
        experiment_id=package.experiment_id,
        package_sha256=package.package_sha256,
        package_root=package.root,
    )
    module_name = _adjudication_module_name(package)
    module = _load_module(module_name, component.root)
    entrypoint = getattr(module, component.entrypoint, None)
    if not callable(entrypoint):
        raise experiment_adjudication_error(
            f"Adjudication entrypoint '{component.entrypoint}' is not callable."
        )

    try:
        adjudicator = entrypoint(context)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise experiment_adjudication_error(
            f"Adjudication entrypoint '{component.entrypoint}' failed: {exc}"
        ) from exc

    _verify_adjudication_snapshot(package)
    if not isinstance(adjudicator, experiment_adjudicator):
        raise experiment_adjudication_error(
            f"Adjudication entrypoint '{component.entrypoint}' must return experiment_adjudicator."
        )

    required = {
        str(check["evaluator"])
        for check in package.contract.get("checks", ())
        if isinstance(check, dict) and isinstance(check.get("evaluator"), str)
    }
    missing = sorted(required - set(adjudicator.evaluators))
    if missing:
        raise experiment_adjudication_error(
            "Adjudicator is missing evaluator(s) required by its Contract: "
            + ", ".join(missing)
        )
    for evaluator_id, evaluator in adjudicator.evaluators.items():
        if not isinstance(evaluator_id, str) or not evaluator_id.strip():
            raise experiment_adjudication_error("Adjudicator evaluator IDs must be non-empty strings.")
        if not callable(getattr(evaluator, "evaluate", None)):
            raise experiment_adjudication_error(
                f"Adjudicator evaluator '{evaluator_id}' does not provide callable evaluate()."
            )

    return loaded_experiment_adjudicator(
        context=context,
        adjudicator=adjudicator,
        module_name=module_name,
    )


def _verify_adjudication_snapshot(package: experiment_package) -> None:
    component = package.adjudication
    assert component is not None
    try:
        verify_staged_component_snapshot(
            package,
            component.root,
            component.sha256,
            "adjudication",
        )
    except staged_package_verification_error as exc:
        message = str(exc)
        if "not an ACMM-staged package snapshot" in message:
            raise experiment_adjudication_error(
                "Experiment adjudication may only be loaded from an ACMM-staged package snapshot."
            ) from exc
        raise experiment_adjudication_error(
            "Staged Experiment Package changed before adjudication activation: " + message
        ) from exc


def _adjudication_module_name(package: experiment_package) -> str:
    root_sha256 = hashlib.sha256(str(package.root).encode("utf-8")).hexdigest()
    return (
        f"_acmm_adjudication_{package.experiment_id}_"
        f"{package.package_sha256[:16]}_{root_sha256[:12]}"
    )


def _load_module(module_name: str, root: Path) -> ModuleType:
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    init_path = root / "__init__.py"
    if not init_path.is_file():
        raise experiment_adjudication_error(
            f"adjudication.root '{root}' must contain '__init__.py'."
        )
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_path,
        submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise experiment_adjudication_error(
            f"Could not create a Python module spec for adjudication '{root}'."
        )

    module = importlib.util.module_from_spec(spec)
    modules_before = set(sys.modules)
    sys.modules[module_name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except BaseException:
        for name in set(sys.modules) - modules_before:
            if name == module_name or name.startswith(module_name + "."):
                sys.modules.pop(name, None)
        raise
    finally:
        sys.dont_write_bytecode = previous
    return module


__all__ = [
    "experiment_adjudication_error",
    "loaded_experiment_adjudicator",
    "load_staged_experiment_adjudicator",
]
