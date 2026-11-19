from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, Callable, Iterable, Mapping

from acmm.experiment_package.model import (
    experiment_integration_component,
    experiment_package,
)
from acmm.experiment_package.snapshot import (
    staged_package_verification_error,
    verify_staged_component_snapshot,
)
from acmm.integration_api import (
    EXPERIMENT_INTEGRATION_API_VERSION,
    experiment_integration,
    experiment_integration_context,
    subject_integration_context,
    tool_result_error_interpreter,
)
from acmm.runtime.interfaces import (
    runtime_subject_admission,
    runtime_subject_admission_request,
)


_SUBJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_REQUIRED_MANAGER_METHODS = ("apply", "status", "doctor", "up", "down")
_REQUIRED_MANAGER_ATTRIBUTES = ("profile", "deployment_root", "docker")


class experiment_integration_error(ValueError):
    """Raised when staged Experiment Package integration code cannot be activated."""


@dataclass(frozen=True)
class loaded_experiment_integration:
    component: experiment_integration_component
    context: experiment_integration_context
    integration: experiment_integration
    module_name: str


def load_staged_experiment_integration(
    package: experiment_package,
    integration_id: str,
) -> loaded_experiment_integration:
    component = _find_component(package, integration_id)
    _verify_integration_snapshot(package, component)

    if component.api_version != EXPERIMENT_INTEGRATION_API_VERSION:
        raise experiment_integration_error(
            f"Unsupported integration API version '{component.api_version}'."
        )

    context = experiment_integration_context(
        experiment_id=package.experiment_id,
        integration_id=component.integration_id,
        preset=component.preset,
        configuration=component.configuration,
        package_sha256=package.package_sha256,
        package_root=package.root,
        secret_ids=tuple(item.secret_id for item in component.secrets),
    )
    module_name = _integration_module_name(package, component)
    module = _load_integration_module(module_name, component.root)
    entrypoint = getattr(module, component.entrypoint, None)

    if not callable(entrypoint):
        raise experiment_integration_error(
            f"Integration entrypoint '{component.entrypoint}' is not callable."
        )

    try:
        integration = entrypoint(context)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise experiment_integration_error(
            f"Integration entrypoint '{component.entrypoint}' failed: {exc}"
        ) from exc

    _verify_integration_snapshot(package, component)

    if not isinstance(integration, experiment_integration):
        raise experiment_integration_error(
            f"Integration entrypoint '{component.entrypoint}' must return "
            "experiment_integration."
        )

    _validate_integration(integration)
    return loaded_experiment_integration(
        component=component,
        context=context,
        integration=integration,
        module_name=module_name,
    )


def load_staged_experiment_integrations(
    package: experiment_package,
) -> tuple[loaded_experiment_integration, ...]:
    return tuple(
        load_staged_experiment_integration(package, component.integration_id)
        for component in package.integrations
    )


def compose_staged_experiment_profile_integrations(
    package: experiment_package,
    infrastructure_subject: str,
    llm_baseline_id: str,
    profile: Any,
    *,
    loaded_integrations: Iterable[loaded_experiment_integration] | None = None,
) -> Any:
    """Compose package integrations into a subject profile."""

    loaded = _resolved_integrations(package, loaded_integrations)
    composed = profile

    for item in loaded:
        component = item.component
        binding = item.integration.subjects.get(infrastructure_subject)
        if binding is None or binding.profile is None:
            continue

        context = _subject_context(
            package,
            component,
            infrastructure_subject,
            llm_baseline_id,
        )
        profile_type = type(composed)
        try:
            realized = binding.profile.compose_profile(context, composed)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise experiment_integration_error(
                f"Integration '{component.integration_id}' failed to compose profile "
                f"for '{infrastructure_subject}': {exc}"
            ) from exc

        if type(realized) is not profile_type:
            raise experiment_integration_error(
                f"Integration '{component.integration_id}' must preserve the subject "
                f"profile type '{profile_type.__name__}'."
            )
        composed = realized

    return composed


def compose_staged_experiment_manager_integrations(
    package: experiment_package,
    infrastructure_subject: str,
    llm_baseline_id: str,
    manager: Any,
    *,
    loaded_integrations: Iterable[loaded_experiment_integration] | None = None,
) -> Any:
    loaded = _resolved_integrations(package, loaded_integrations)
    composed = manager

    _validate_manager_shape(composed, integration_id=None)
    baseline_deployment_root = composed.deployment_root
    baseline_docker = composed.docker

    for item in loaded:
        component = item.component
        binding = item.integration.subjects.get(infrastructure_subject)
        if binding is None or binding.manager is None:
            continue

        context = _subject_context(
            package,
            component,
            infrastructure_subject,
            llm_baseline_id,
        )
        try:
            realized = binding.manager.compose_manager(context, composed)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise experiment_integration_error(
                f"Integration '{component.integration_id}' failed to compose manager "
                f"for '{infrastructure_subject}': {exc}"
            ) from exc

        _validate_manager_shape(realized, integration_id=component.integration_id)
        if realized.deployment_root != baseline_deployment_root:
            raise experiment_integration_error(
                f"Integration '{component.integration_id}' must preserve the subject "
                "deployment root."
            )
        if realized.docker is not baseline_docker:
            raise experiment_integration_error(
                f"Integration '{component.integration_id}' must preserve the subject "
                "Docker client identity."
            )
        composed = realized

    return composed


@dataclass(frozen=True)
class _runtime_admission_entry:
    integration_id: str
    context: subject_integration_context
    admission: Any
    manager: Any
    handle: Any


class staged_subject_admission_controller:
    def __init__(
        self,
        package: experiment_package,
        subject_contexts: Mapping[str, tuple[str, str]],
        manager_resolver: Callable[[str], Any],
        *,
        loaded_integrations: Iterable[loaded_experiment_integration] | None = None,
    ) -> None:
        self._package = package
        self._subject_contexts = dict(subject_contexts)
        self._manager_resolver = manager_resolver
        self._loaded = _resolved_integrations(package, loaded_integrations)
        self._by_id = {item.component.integration_id: item for item in self._loaded}

    @property
    def is_required(self) -> bool:
        for subject_id, (infrastructure_subject, _baseline_id) in self._subject_contexts.items():
            del subject_id
            for item in self._loaded:
                binding = item.integration.subjects.get(infrastructure_subject)
                if binding is not None and binding.admission is not None:
                    return True
        return False

    def admit(
        self,
        subject_id: str,
        requests: tuple[runtime_subject_admission_request, ...],
    ) -> runtime_subject_admission:
        if subject_id not in self._subject_contexts:
            raise experiment_integration_error(
                f"Runtime admission subject '{subject_id}' is not configured."
            )
        integration_ids = [request.integration_id for request in requests]
        if len(integration_ids) != len(set(integration_ids)):
            raise experiment_integration_error(
                "Runtime admission requests must have unique integration ids."
            )

        infrastructure_subject, baseline_id = self._subject_contexts[subject_id]
        manager = self._manager_resolver(subject_id)
        entries: list[_runtime_admission_entry] = []
        try:
            for request in requests:
                item = self._by_id.get(request.integration_id)
                if item is None:
                    raise experiment_integration_error(
                        f"Runtime admission requested unknown integration "
                        f"'{request.integration_id}'."
                    )
                binding = item.integration.subjects.get(infrastructure_subject)
                admission = None if binding is None else binding.admission
                if admission is None:
                    raise experiment_integration_error(
                        f"Integration '{request.integration_id}' does not provide "
                        f"runtime admission for '{infrastructure_subject}'."
                    )
                context = _subject_context(
                    self._package,
                    item.component,
                    infrastructure_subject,
                    baseline_id,
                )
                handle = admission.admit(context, manager, request.payload)
                entries.append(
                    _runtime_admission_entry(
                        integration_id=request.integration_id,
                        context=context,
                        admission=admission,
                        manager=manager,
                        handle=handle,
                    )
                )
        except BaseException as exc:
            cleanup_errors = self._revoke_entries(entries)
            if isinstance(exc, KeyboardInterrupt):
                raise
            detail = str(exc) or exc.__class__.__name__
            if cleanup_errors:
                detail += " Runtime admission cleanup also failed: " + "; ".join(cleanup_errors)
            raise experiment_integration_error(detail) from exc

        return runtime_subject_admission(
            handle=tuple(entries),
            provenance={
                "integrations": tuple(entry.integration_id for entry in entries),
            },
        )

    def revoke(
        self,
        subject_id: str,
        admission: runtime_subject_admission,
    ) -> Mapping[str, Any]:
        del subject_id
        entries = admission.handle
        if not isinstance(entries, tuple) or any(
            not isinstance(entry, _runtime_admission_entry) for entry in entries
        ):
            raise experiment_integration_error(
                "Runtime admission handle is invalid for staged integrations."
            )
        observations: dict[str, Any] = {}
        errors: list[str] = []
        for entry in reversed(entries):
            try:
                value = entry.admission.revoke(
                    entry.context,
                    entry.manager,
                    entry.handle,
                )
                if value is not None:
                    if not isinstance(value, Mapping):
                        raise TypeError(
                            "runtime admission revoke() must return a mapping or None"
                        )
                    observations[entry.integration_id] = dict(value)
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    raise
                errors.append(
                    f"{entry.integration_id}: {str(exc) or exc.__class__.__name__}"
                )
        if errors:
            raise experiment_integration_error(
                "Runtime subject admission revocation failed: " + "; ".join(errors)
            )
        return {"integrations": observations}

    @staticmethod
    def _revoke_entries(entries: list[_runtime_admission_entry]) -> tuple[str, ...]:
        errors: list[str] = []
        for entry in reversed(entries):
            try:
                entry.admission.revoke(entry.context, entry.manager, entry.handle)
            except BaseException as exc:
                errors.append(
                    f"{entry.integration_id}: {str(exc) or exc.__class__.__name__}"
                )
        return tuple(errors)


def compose_staged_subject_admission_controller(
    package: experiment_package,
    subject_contexts: Mapping[str, tuple[str, str]],
    manager_resolver: Callable[[str], Any],
    *,
    loaded_integrations: Iterable[loaded_experiment_integration] | None = None,
) -> staged_subject_admission_controller | None:
    controller = staged_subject_admission_controller(
        package,
        subject_contexts,
        manager_resolver,
        loaded_integrations=loaded_integrations,
    )
    return controller if controller.is_required else None


def _resolved_integrations(
    package: experiment_package,
    loaded_integrations: Iterable[loaded_experiment_integration] | None,
) -> tuple[loaded_experiment_integration, ...]:
    if loaded_integrations is None:
        return load_staged_experiment_integrations(package)

    loaded = tuple(loaded_integrations)
    expected = tuple(component.integration_id for component in package.integrations)
    observed = tuple(item.component.integration_id for item in loaded)
    if observed != expected:
        raise experiment_integration_error(
            "Loaded Experiment Package integrations do not match manifest order."
        )
    return loaded


def staged_tool_result_error_interpreters(
    package: experiment_package,
    *,
    loaded_integrations: Iterable[loaded_experiment_integration] | None = None,
) -> tuple[tool_result_error_interpreter, ...]:
    loaded = _resolved_integrations(package, loaded_integrations)
    return tuple(
        interpreter
        for item in loaded
        for interpreter in item.integration.tool_result_error_interpreters
    )


def _subject_context(
    package: experiment_package,
    component: experiment_integration_component,
    infrastructure_subject: str,
    llm_baseline_id: str,
) -> subject_integration_context:
    return subject_integration_context(
        experiment_id=package.experiment_id,
        integration_id=component.integration_id,
        preset=component.preset,
        configuration=component.configuration,
        package_sha256=package.package_sha256,
        package_root=package.root,
        infrastructure_subject=infrastructure_subject,
        llm_baseline_id=llm_baseline_id,
        secret_ids=tuple(item.secret_id for item in component.secrets),
    )


def _find_component(
    package: experiment_package,
    integration_id: str,
) -> experiment_integration_component:
    for component in package.integrations:
        if component.integration_id == integration_id:
            return component
    raise experiment_integration_error(
        f"Experiment Package '{package.experiment_id}' does not declare integration "
        f"'{integration_id}'."
    )


def _verify_integration_snapshot(
    package: experiment_package,
    component: experiment_integration_component,
) -> None:
    try:
        verify_staged_component_snapshot(
            package,
            component.root,
            component.sha256,
            f"integration '{component.integration_id}'",
        )
    except staged_package_verification_error as exc:
        message = str(exc)
        if "not an ACMM-staged package snapshot" in message:
            raise experiment_integration_error(
                "Experiment integration may only be loaded from an ACMM-staged "
                "package snapshot."
            ) from exc
        raise experiment_integration_error(
            "Staged Experiment Package changed before integration activation: " + message
        ) from exc


def _integration_module_name(
    package: experiment_package,
    component: experiment_integration_component,
) -> str:
    root_sha256 = hashlib.sha256(str(package.root).encode("utf-8")).hexdigest()
    safe_id = component.integration_id.replace("-", "_")
    return (
        f"_acmm_integration_{package.experiment_id}_{safe_id}_"
        f"{component.sha256[:16]}_{root_sha256[:12]}"
    )


def _load_integration_module(
    module_name: str,
    integration_root: Path,
) -> ModuleType:
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    init_path = integration_root / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_path,
        submodule_search_locations=[str(integration_root)],
    )

    if spec is None or spec.loader is None:
        raise experiment_integration_error(
            f"Could not create a Python module spec for integration "
            f"'{integration_root}'."
        )

    module = importlib.util.module_from_spec(spec)
    modules_before = set(sys.modules)
    sys.modules[module_name] = module

    previous_bytecode_setting = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except BaseException as exc:
        _remove_failed_integration_modules(module_name, modules_before)
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise experiment_integration_error(
            f"Could not import Experiment Package integration "
            f"'{integration_root}': {exc}"
        ) from exc
    finally:
        sys.dont_write_bytecode = previous_bytecode_setting

    return module


def _remove_failed_integration_modules(
    module_name: str,
    modules_before: set[str],
) -> None:
    prefix = module_name + "."
    for name in tuple(sys.modules):
        if name in modules_before:
            continue
        if name == module_name or name.startswith(prefix):
            sys.modules.pop(name, None)


def _validate_integration(integration: experiment_integration) -> None:
    resource = integration.shared_resource
    if resource is not None:
        for method in ("apply", "status", "doctor", "down", "remove"):
            if not callable(getattr(resource, method, None)):
                raise experiment_integration_error(
                    f"Integration shared resource does not provide callable {method}()."
                )

    for interpreter in integration.tool_result_error_interpreters:
        if not callable(getattr(interpreter, "detects_error", None)):
            raise experiment_integration_error(
                "Integration tool-result error interpreter does not provide "
                "callable detects_error()."
            )

    for subject, binding in integration.subjects.items():
        if (
            not isinstance(subject, str)
            or _SUBJECT_ID_PATTERN.fullmatch(subject) is None
        ):
            raise experiment_integration_error(
                f"Integration subject identifier is invalid: {subject!r}."
            )

        profile = getattr(binding, "profile", None)
        manager = getattr(binding, "manager", None)
        admission = getattr(binding, "admission", None)
        if profile is None and manager is None and admission is None:
            raise experiment_integration_error(
                f"Integration subject '{subject}' must provide profile, manager, "
                "and/or runtime admission behavior."
            )
        if profile is not None and not callable(getattr(profile, "compose_profile", None)):
            raise experiment_integration_error(
                f"Integration subject '{subject}' profile integration does not provide "
                "callable compose_profile()."
            )
        if manager is not None and not callable(getattr(manager, "compose_manager", None)):
            raise experiment_integration_error(
                f"Integration subject '{subject}' manager integration does not provide "
                "callable compose_manager()."
            )
        if admission is not None:
            for method in ("admit", "revoke"):
                if not callable(getattr(admission, method, None)):
                    raise experiment_integration_error(
                        f"Integration subject '{subject}' runtime admission does not "
                        f"provide callable {method}()."
                    )


def _validate_manager_shape(manager: Any, integration_id: str | None) -> None:
    label = (
        f"Integration '{integration_id}' manager"
        if integration_id is not None
        else "Subject manager"
    )
    for attribute in _REQUIRED_MANAGER_ATTRIBUTES:
        if not hasattr(manager, attribute):
            raise experiment_integration_error(
                f"{label} does not provide required attribute '{attribute}'."
            )
    for method in _REQUIRED_MANAGER_METHODS:
        if not callable(getattr(manager, method, None)):
            raise experiment_integration_error(
                f"{label} does not provide callable {method}()."
            )
