from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, Callable, Mapping

from acmm.contract.compatibility import validate_harness_compatibility
from acmm.experiment_api import (
    EXPERIMENT_RUNTIME_API_VERSION,
    apparatus_action_progress,
    apparatus_action_result,
    experiment_runtime,
    experiment_runtime_context,
    runtime_diagnostic_check,
    runtime_diagnostic_report,
)
from acmm.experiment_package.model import experiment_package
from acmm.integration_api import tool_result_error_interpreter
from acmm.experiment_package.snapshot import (
    staged_package_verification_error,
    verify_staged_component_snapshot,
)
from acmm.runtime.interfaces import subject_adapter
from acmm.runtime.registry import execution_registry


_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class experiment_runtime_error(ValueError):
    """Raised when staged Experiment Package runtime code cannot be activated."""


@dataclass(frozen=True)
class loaded_experiment_runtime:
    context: experiment_runtime_context
    runtime: experiment_runtime
    module_name: str


def load_staged_experiment_runtime(
    package: experiment_package,
) -> loaded_experiment_runtime:
    component = package.runtime
    if component is None:
        raise experiment_runtime_error(
            f"Experiment Package '{package.experiment_id}' does not declare runtime code."
        )

    _verify_runtime_snapshot(package)

    if component.api_version != EXPERIMENT_RUNTIME_API_VERSION:
        raise experiment_runtime_error(
            f"Unsupported runtime API version '{component.api_version}'."
        )

    context = experiment_runtime_context(
        experiment_id=package.experiment_id,
        package_sha256=package.package_sha256,
        package_root=package.root,
        package_secret_ids=tuple(secret.secret_id for secret in package.secrets),
        integration_secret_ids={
            component.integration_id: tuple(
                secret.secret_id for secret in component.secrets
            )
            for component in package.integrations
        },
    )
    module_name = _runtime_module_name(package)
    module = _load_runtime_module(
        module_name,
        component.root,
    )
    entrypoint = getattr(module, component.entrypoint, None)

    if not callable(entrypoint):
        raise experiment_runtime_error(
            f"Runtime entrypoint '{component.entrypoint}' is not callable."
        )

    try:
        runtime = entrypoint(context)
    except Exception as exc:
        raise experiment_runtime_error(
            f"Runtime entrypoint '{component.entrypoint}' failed: {exc}"
        ) from exc

    _verify_runtime_snapshot(package)

    if not isinstance(runtime, experiment_runtime):
        raise experiment_runtime_error(
            f"Runtime entrypoint '{component.entrypoint}' must return experiment_runtime."
        )

    _validate_runtime_contributions(runtime)
    compatibility = validate_harness_compatibility(
        dict(package.contract),
        runtime.capabilities,
    )

    if compatibility.errors:
        issue = compatibility.errors[0]
        raise experiment_runtime_error(
            "Experiment runtime is incompatible with its Contract at "
            f"'{issue.path}': {issue.message}"
        )

    return loaded_experiment_runtime(
        context=context,
        runtime=runtime,
        module_name=module_name,
    )


def doctor_staged_experiment_runtime(
    package: experiment_package,
) -> tuple[tuple[str, runtime_diagnostic_report], ...]:
    if package.runtime is None:
        return ()

    loaded = load_staged_experiment_runtime(package)
    reports: list[tuple[str, runtime_diagnostic_report]] = []
    for diagnostic_id, diagnostic in loaded.runtime.diagnostics.items():
        try:
            report = diagnostic.doctor()
        except Exception as exc:
            detail = str(exc) or exc.__class__.__name__
            report = runtime_diagnostic_report(
                checks=(
                    runtime_diagnostic_check(
                        name="diagnostic_execution",
                        ok=False,
                        detail=detail,
                    ),
                )
            )
        if not isinstance(report, runtime_diagnostic_report):
            report = runtime_diagnostic_report(
                checks=(
                    runtime_diagnostic_check(
                        name="diagnostic_protocol",
                        ok=False,
                        detail=(
                            "Runtime diagnostic doctor() must return "
                            "runtime_diagnostic_report."
                        ),
                    ),
                )
            )
        reports.append((diagnostic_id, report))
    return tuple(reports)



def run_staged_experiment_apparatus_action(
    package: experiment_package,
    action_id: str,
    *,
    progress: Callable[[apparatus_action_progress], None] | None = None,
) -> apparatus_action_result:
    loaded = load_staged_experiment_runtime(package)
    action = loaded.runtime.apparatus_actions.get(action_id)
    if action is None:
        available = ", ".join(sorted(loaded.runtime.apparatus_actions)) or "none"
        raise experiment_runtime_error(
            f"Experiment Package '{package.experiment_id}' does not provide apparatus "
            f"action '{action_id}'. Available actions: {available}."
        )

    try:
        progressive = getattr(action, "run_with_progress", None)
        if progress is not None and callable(progressive):
            result = progressive(progress)
        else:
            result = action.run()
    except Exception as exc:
        raise experiment_runtime_error(
            f"Experiment apparatus action '{action_id}' failed: {exc}"
        ) from exc

    if not isinstance(result, apparatus_action_result):
        raise experiment_runtime_error(
            f"Experiment apparatus action '{action_id}' must return "
            "apparatus_action_result."
        )
    return result

def run_staged_experiment_apparatus_cleanup(
    package: experiment_package,
    execution_path: str,
    *,
    preview: bool = False,
    progress: Callable[[apparatus_action_progress], None] | None = None,
) -> apparatus_action_result:
    loaded = load_staged_experiment_runtime(package)
    cleanup = loaded.runtime.apparatus_cleanup
    if cleanup is None:
        raise experiment_runtime_error(
            f"Experiment Package '{package.experiment_id}' does not provide "
            "apparatus cleanup support."
        )

    try:
        progressive = getattr(cleanup, "cleanup_with_progress", None)
        if progress is not None and callable(progressive):
            result = progressive(
                execution_path,
                progress,
                preview=preview,
            )
        else:
            result = cleanup.cleanup(execution_path, preview=preview)
    except Exception as exc:
        raise experiment_runtime_error(
            f"Experiment apparatus cleanup failed: {exc}"
        ) from exc

    if not isinstance(result, apparatus_action_result):
        raise experiment_runtime_error(
            "Experiment apparatus cleanup must return apparatus_action_result."
        )
    return result


def compose_execution_registry(
    runtime: experiment_runtime,
    subject_adapters: Mapping[str, subject_adapter],
    *,
    tool_result_error_interpreters: tuple[tool_result_error_interpreter, ...] = (),
    subject_configuration_overlays: Mapping[str, Mapping[str, Any]] | None = None,
    admission_controller: Any = None,
    materialization_group_by: tuple[str, ...] = ("run_index",),
) -> execution_registry:
    overlays = {
        subject_id: dict(configuration)
        for subject_id, configuration in (subject_configuration_overlays or {}).items()
    }

    return execution_registry(
        subject_adapters=dict(subject_adapters),
        lifecycle=runtime.lifecycle,
        resource_governor=runtime.resource_governor,
        materialization_group_by=tuple(materialization_group_by),
        admission_controller=admission_controller,
        fixtures=dict(runtime.fixtures),
        evaluators=dict(runtime.evaluators),
        tool_result_error_interpreters=tuple(tool_result_error_interpreters),
        subject_configuration_overlays=overlays,
    )


def _verify_runtime_snapshot(package: experiment_package) -> None:
    component = package.runtime
    assert component is not None

    try:
        verify_staged_component_snapshot(
            package,
            component.root,
            component.sha256,
            "runtime",
        )
    except staged_package_verification_error as exc:
        message = str(exc)
        if "not an ACMM-staged package snapshot" in message:
            raise experiment_runtime_error(
                "Experiment runtime may only be loaded from an ACMM-staged package snapshot."
            ) from exc
        raise experiment_runtime_error(
            "Staged Experiment Package changed before runtime activation: " + message
        ) from exc


def _runtime_module_name(package: experiment_package) -> str:
    root_sha256 = hashlib.sha256(str(package.root).encode("utf-8")).hexdigest()
    return (
        f"_acmm_experiment_{package.experiment_id}_"
        f"{package.package_sha256[:16]}_{root_sha256[:12]}"
    )


def _load_runtime_module(
    module_name: str,
    runtime_root: Path,
) -> ModuleType:
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    init_path = runtime_root / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_path,
        submodule_search_locations=[str(runtime_root)],
    )

    if spec is None or spec.loader is None:
        raise experiment_runtime_error(
            f"Could not create a Python module spec for runtime '{runtime_root}'."
        )

    module = importlib.util.module_from_spec(spec)
    modules_before = set(sys.modules)
    sys.modules[module_name] = module

    previous_bytecode_setting = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        _remove_failed_runtime_modules(module_name, modules_before)
        raise experiment_runtime_error(
            f"Could not import Experiment Package runtime '{runtime_root}': {exc}"
        ) from exc
    finally:
        sys.dont_write_bytecode = previous_bytecode_setting

    return module


def _remove_failed_runtime_modules(
    module_name: str,
    modules_before: set[str],
) -> None:
    prefix = module_name + "."
    for name in tuple(sys.modules):
        if name in modules_before:
            continue
        if name == module_name or name.startswith(prefix):
            sys.modules.pop(name, None)


def _validate_runtime_contributions(runtime: experiment_runtime) -> None:
    if not isinstance(runtime.capabilities.evaluators, frozenset):
        raise experiment_runtime_error("Runtime evaluator capabilities must be a frozenset.")
    if not isinstance(runtime.capabilities.fixtures, frozenset):
        raise experiment_runtime_error("Runtime fixture capabilities must be a frozenset.")
    if not isinstance(runtime.capabilities.evidence_channels, frozenset):
        raise experiment_runtime_error(
            "Runtime evidence-channel capabilities must be a frozenset."
        )

    for label, values in (
        ("evaluator", runtime.capabilities.evaluators),
        ("fixture", runtime.capabilities.fixtures),
        ("evidence channel", runtime.capabilities.evidence_channels),
    ):
        _validate_capability_identifiers(label, values)

    fixture_ids = frozenset(runtime.fixtures)
    evaluator_ids = frozenset(runtime.evaluators)
    diagnostic_ids = frozenset(runtime.diagnostics)
    apparatus_action_ids = frozenset(runtime.apparatus_actions)
    _validate_capability_identifiers("diagnostic", diagnostic_ids)
    _validate_capability_identifiers("apparatus action", apparatus_action_ids)

    if fixture_ids != runtime.capabilities.fixtures:
        raise experiment_runtime_error(
            "Runtime fixture declarations must exactly match runtime.capabilities.fixtures."
        )
    if evaluator_ids != runtime.capabilities.evaluators:
        raise experiment_runtime_error(
            "Runtime evaluator declarations must exactly match runtime.capabilities.evaluators."
        )

    if runtime.lifecycle is not None:
        for method_name in (
            "session_initialize",
            "run_prepare",
            "run_observe",
            "run_finalize",
            "session_finalize",
        ):
            if not callable(getattr(runtime.lifecycle, method_name, None)):
                raise experiment_runtime_error(
                    "Runtime execution lifecycle does not provide callable "
                    f"{method_name}()."
                )

        materialization_initialize = callable(
            getattr(runtime.lifecycle, "materialization_initialize", None)
        )
        materialization_finalize = callable(
            getattr(runtime.lifecycle, "materialization_finalize", None)
        )
        if materialization_initialize != materialization_finalize:
            raise experiment_runtime_error(
                "Runtime materialization lifecycle must provide both callable "
                "materialization_initialize() and materialization_finalize(), or neither."
            )

    if runtime.resource_governor is not None:
        for method_name in (
            "execution_initialize",
            "materialization_admit",
            "materialization_finalize",
        ):
            if not callable(getattr(runtime.resource_governor, method_name, None)):
                raise experiment_runtime_error(
                    "Runtime resource governor does not provide callable "
                    f"{method_name}()."
                )

    if runtime.recovery is not None:
        if not callable(getattr(runtime.recovery, "reconcile", None)):
            raise experiment_runtime_error(
                "Runtime execution recovery does not provide callable reconcile()."
            )

    if runtime.apparatus_cleanup is not None:
        if not callable(getattr(runtime.apparatus_cleanup, "cleanup", None)):
            raise experiment_runtime_error(
                "Runtime apparatus cleanup does not provide callable cleanup()."
            )

    for fixture_id, fixture in runtime.fixtures.items():
        if not callable(getattr(fixture, "prepare", None)):
            raise experiment_runtime_error(
                f"Runtime fixture '{fixture_id}' does not provide callable prepare()."
            )

    for evaluator_id, evaluator in runtime.evaluators.items():
        if not callable(getattr(evaluator, "evaluate", None)):
            raise experiment_runtime_error(
                f"Runtime evaluator '{evaluator_id}' does not provide callable evaluate()."
            )

    for diagnostic_id, diagnostic in runtime.diagnostics.items():
        if not callable(getattr(diagnostic, "doctor", None)):
            raise experiment_runtime_error(
                f"Runtime diagnostic '{diagnostic_id}' does not provide callable doctor()."
            )

    for action_id, action in runtime.apparatus_actions.items():
        if not callable(getattr(action, "run", None)):
            raise experiment_runtime_error(
                f"Runtime apparatus action '{action_id}' does not provide callable run()."
            )


def _validate_capability_identifiers(
    label: str,
    values: frozenset[str],
) -> None:
    for value in values:
        if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
            raise experiment_runtime_error(
                f"Runtime {label} capability is not a valid ACMM identifier: {value!r}."
            )
