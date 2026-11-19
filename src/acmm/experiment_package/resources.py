from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any, Mapping

from acmm.experiment_package.integration import (
    experiment_integration_error,
    load_staged_experiment_integrations,
)
from acmm.experiment_package.model import experiment_package
from acmm.integration_api import integration_report, shared_integration_context
from acmm.runtime.interruption import protect_interruption_cleanup
from acmm.secret_bindings import (
    experiment_secret_binding_key,
    resolve_secret,
    secret_binding_key,
)


_RESOURCE_DIRECTORY = "integration-resources"


@dataclass(frozen=True)
class integration_resource_result:
    integration_id: str
    value: Mapping[str, Any]


@dataclass(frozen=True)
class integration_resource_lease:
    integration_id: str
    initially_running: bool
    started_by_acmm: bool


@dataclass(frozen=True)
class integration_resource_down_result:
    integration_id: str
    initially_running: bool | None
    running_after: bool | None
    value: Mapping[str, Any]
    error: str | None = None


def validate_staged_package_secrets(package: experiment_package) -> None:
    for secret in package.secrets:
        if not secret.required:
            continue
        resolve_secret(
            experiment_secret_binding_key(
                experiment_id=package.experiment_id,
                secret_id=secret.secret_id,
            )
        )
    validate_staged_integration_secrets(package)


def validate_staged_integration_secrets(package: experiment_package) -> None:
    for component in package.integrations:
        for secret in component.secrets:
            if not secret.required:
                continue
            resolve_secret(
                secret_binding_key(
                    experiment_id=package.experiment_id,
                    integration_id=component.integration_id,
                    secret_id=secret.secret_id,
                )
            )


def apply_staged_integration_resources(
    package: experiment_package,
    runtime_root: str | Path,
    *,
    timeout_seconds: float = 120.0,
    progress=None,
) -> tuple[integration_resource_result, ...]:
    loaded = load_staged_experiment_integrations(package)
    results: list[integration_resource_result] = []
    for item in loaded:
        resource = item.integration.shared_resource
        if resource is None:
            continue
        integration_id = item.component.integration_id
        context = _context(package, integration_id, runtime_root)
        context.state_root.mkdir(parents=True, exist_ok=True)
        try:
            value = resource.apply(
                context,
                timeout_seconds=timeout_seconds,
                progress=(
                    None
                    if progress is None
                    else lambda message, iid=integration_id: progress(iid, message)
                ),
            )
        except Exception as exc:
            raise experiment_integration_error(
                f"Integration '{integration_id}' shared resource apply failed: {exc}"
            ) from exc
        results.append(
            integration_resource_result(
                integration_id=integration_id,
                value=dict(value),
            )
        )
    return tuple(results)


def acquire_staged_integration_resources(
    package: experiment_package,
    runtime_root: str | Path,
    *,
    start_if_needed: bool,
    timeout_seconds: float = 120.0,
    progress=None,
) -> tuple[integration_resource_lease, ...]:
    loaded = load_staged_experiment_integrations(package)
    leases: list[integration_resource_lease] = []
    for item in loaded:
        resource = item.integration.shared_resource
        if resource is None:
            continue
        integration_id = item.component.integration_id
        context = _context(package, integration_id, runtime_root)
        initially_running = False
        started_by_acmm = False
        try:
            initially_running = bool(resource.is_running(context))
            if progress is not None:
                progress(
                    integration_id,
                    "runtime-running" if initially_running else "runtime-stopped",
                )
            if start_if_needed and not initially_running:
                if progress is not None:
                    progress(integration_id, "starting")
                resource.up(
                    context,
                    timeout_seconds=timeout_seconds,
                    progress=(
                        None
                        if progress is None
                        else lambda message, iid=integration_id: progress(iid, message)
                    ),
                )
                started_by_acmm = True
                if progress is not None:
                    progress(integration_id, "started")
        except BaseException as exc:
            cleanup_errors: list[str] = []
            cleanup_scope = (
                protect_interruption_cleanup()
                if isinstance(exc, KeyboardInterrupt)
                else nullcontext()
            )
            with cleanup_scope:
                loaded_by_id = {
                    value.component.integration_id: value
                    for value in loaded
                }
                if start_if_needed and not initially_running:
                    try:
                        if resource.is_running(context):
                            resource.down(context)
                    except Exception as cleanup_exc:
                        cleanup_errors.append(
                            f"{integration_id}: {cleanup_exc}"
                        )
                for previous in reversed(leases):
                    if not previous.started_by_acmm:
                        continue
                    previous_item = loaded_by_id.get(previous.integration_id)
                    if previous_item is None or previous_item.integration.shared_resource is None:
                        continue
                    try:
                        previous_item.integration.shared_resource.down(
                            _context(package, previous.integration_id, runtime_root)
                        )
                    except Exception as cleanup_exc:
                        cleanup_errors.append(
                            f"{previous.integration_id}: {cleanup_exc}"
                        )
            if isinstance(exc, KeyboardInterrupt):
                raise
            detail = (
                f"Integration '{integration_id}' shared resource startup failed: {exc}"
            )
            if cleanup_errors:
                detail += "; rollback failed for " + "; ".join(cleanup_errors)
            raise experiment_integration_error(detail) from exc
        leases.append(
            integration_resource_lease(
                integration_id=integration_id,
                initially_running=initially_running,
                started_by_acmm=started_by_acmm,
            )
        )
    return tuple(leases)


def restore_staged_integration_resources(
    package: experiment_package,
    runtime_root: str | Path,
    leases: tuple[integration_resource_lease, ...],
    *,
    progress=None,
) -> tuple[tuple[str, str], ...]:
    loaded = {
        item.component.integration_id: item
        for item in load_staged_experiment_integrations(package)
    }
    errors: list[tuple[str, str]] = []
    for lease in reversed(leases):
        if not lease.started_by_acmm:
            continue
        item = loaded.get(lease.integration_id)
        if item is None or item.integration.shared_resource is None:
            errors.append((lease.integration_id, "shared resource is no longer available"))
            continue
        if progress is not None:
            progress(lease.integration_id, "restoring")
        try:
            item.integration.shared_resource.down(
                _context(package, lease.integration_id, runtime_root)
            )
            if progress is not None:
                progress(lease.integration_id, "restored")
        except Exception as exc:
            errors.append((lease.integration_id, str(exc)))
            if progress is not None:
                progress(lease.integration_id, "restore-failed")
    return tuple(errors)


def doctor_staged_integration_resources(
    package: experiment_package,
    runtime_root: str | Path,
) -> tuple[tuple[str, integration_report], ...]:
    loaded = load_staged_experiment_integrations(package)
    reports: list[tuple[str, integration_report]] = []
    for item in loaded:
        resource = item.integration.shared_resource
        if resource is None:
            continue
        report = resource.doctor(
            _context(package, item.component.integration_id, runtime_root)
        )
        if not isinstance(report, integration_report):
            raise experiment_integration_error(
                f"Integration '{item.component.integration_id}' shared resource doctor "
                "must return integration_report."
            )
        reports.append((item.component.integration_id, report))
    return tuple(reports)


def status_staged_integration_resources(
    package: experiment_package,
    runtime_root: str | Path,
) -> tuple[integration_resource_result, ...]:
    loaded = load_staged_experiment_integrations(package)
    results: list[integration_resource_result] = []
    for item in loaded:
        resource = item.integration.shared_resource
        if resource is None:
            continue
        value = resource.status(
            _context(package, item.component.integration_id, runtime_root)
        )
        results.append(
            integration_resource_result(
                integration_id=item.component.integration_id,
                value=dict(value),
            )
        )
    return tuple(results)


def down_staged_integration_resources(
    package: experiment_package,
    runtime_root: str | Path,
) -> tuple[integration_resource_down_result, ...]:
    loaded = load_staged_experiment_integrations(package)
    results: list[integration_resource_down_result] = []
    for item in reversed(loaded):
        resource = item.integration.shared_resource
        if resource is None:
            continue

        integration_id = item.component.integration_id
        context = _context(package, integration_id, runtime_root)
        initially_running: bool | None = None
        running_after: bool | None = None
        value: Mapping[str, Any] = {}
        errors: list[str] = []

        try:
            initially_running = bool(resource.is_running(context))
        except Exception as exc:
            errors.append(f"initial state: {exc}")

        try:
            resource.down(context)
        except Exception as exc:
            errors.append(f"shutdown: {exc}")

        try:
            running_after = bool(resource.is_running(context))
            value = dict(resource.status(context))
        except Exception as exc:
            errors.append(f"final state: {exc}")

        results.append(
            integration_resource_down_result(
                integration_id=integration_id,
                initially_running=initially_running,
                running_after=running_after,
                value=value,
                error="; ".join(errors) if errors else None,
            )
        )
    return tuple(results)


def remove_staged_integration_resources(
    package: experiment_package,
    runtime_root: str | Path,
) -> None:
    loaded = load_staged_experiment_integrations(package)
    for item in reversed(loaded):
        resource = item.integration.shared_resource
        if resource is not None:
            resource.remove(_context(package, item.component.integration_id, runtime_root))



def remove_superseded_staged_integration_resources(
    package: experiment_package,
    replacement: experiment_package,
    runtime_root: str | Path,
) -> None:
    """Remove resources unique to an old revision and purge its local state.

    Integrations with the same logical ID in the replacement are treated as
    continuous ownership. Their external resource is left intact so the
    replacement revision can own it without an upgrade-time teardown.
    """

    replacement_loaded = load_staged_experiment_integrations(replacement)
    replacement_resource_ids = {
        item.component.integration_id
        for item in replacement_loaded
        if item.integration.shared_resource is not None
    }
    loaded = load_staged_experiment_integrations(package)
    for item in reversed(loaded):
        integration_id = item.component.integration_id
        context = _context(package, integration_id, runtime_root)
        resource = item.integration.shared_resource
        if resource is not None and integration_id not in replacement_resource_ids:
            resource.remove(context)
        if context.state_root.exists():
            shutil.rmtree(context.state_root)

    resource_root = (
        Path(runtime_root).expanduser().resolve()
        / _RESOURCE_DIRECTORY
        / package.experiment_id
    )
    if resource_root.exists():
        for integration_root in tuple(resource_root.iterdir()):
            if integration_root.is_dir() and not any(integration_root.iterdir()):
                integration_root.rmdir()
        if not any(resource_root.iterdir()):
            resource_root.rmdir()
        parent = resource_root.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

def _context(
    package: experiment_package,
    integration_id: str,
    runtime_root: str | Path,
) -> shared_integration_context:
    component = next(
        item for item in package.integrations if item.integration_id == integration_id
    )
    runtime = Path(runtime_root).expanduser().resolve()
    state_root = (
        runtime
        / _RESOURCE_DIRECTORY
        / package.experiment_id
        / integration_id
        / package.package_sha256
    )
    return shared_integration_context(
        experiment_id=package.experiment_id,
        integration_id=integration_id,
        preset=component.preset,
        configuration=component.configuration,
        package_sha256=package.package_sha256,
        package_root=package.root,
        runtime_root=runtime,
        state_root=state_root,
        secret_ids=tuple(item.secret_id for item in component.secrets),
    )


__all__ = [
    "acquire_staged_integration_resources",
    "apply_staged_integration_resources",
    "doctor_staged_integration_resources",
    "down_staged_integration_resources",
    "integration_resource_down_result",
    "integration_resource_lease",
    "integration_resource_result",
    "remove_staged_integration_resources",
    "remove_superseded_staged_integration_resources",
    "restore_staged_integration_resources",
    "status_staged_integration_resources",
    "validate_staged_integration_secrets",
    "validate_staged_package_secrets",
]
