from __future__ import annotations

import hashlib
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator
import yaml

from acmm.contract.loader import contract_load_error, load_contract
from acmm.contract.validator import load_schema_for_contract, validate_contract
from acmm.experiment_api import EXPERIMENT_RUNTIME_API_VERSION
from acmm.integration_api import EXPERIMENT_INTEGRATION_API_VERSION
from acmm.experiment_package.model import (
    experiment_analysis_component,
    experiment_integration_component,
    experiment_integration_secret,
    experiment_package,
    experiment_runtime_component,
)
from acmm.experiment_package.schema import load_package_manifest_schema
from acmm.experiment_package.source_tree import (
    component_tree_sha256,
    is_managed_package_path,
    package_tree_sha256,
    validate_package_tree,
)


_PACKAGE_MANIFEST_NAME = "experiment-package.yaml"
_EXPERIMENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_COMPONENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class experiment_package_error(ValueError):
    """Raised when an Experiment Package cannot be loaded or validated."""


def load_experiment_package(
    package_root: str | Path,
    *,
    contract_schema_path: str | Path | None = None,
) -> experiment_package:
    unresolved_root = Path(package_root).expanduser()
    if unresolved_root.is_symlink():
        raise experiment_package_error(
            "Experiment Package root must not be a symbolic link."
        )

    root = unresolved_root.resolve()

    if not root.is_dir():
        raise experiment_package_error(
            f"Experiment Package root does not exist or is not a directory: '{root}'."
        )

    try:
        validate_package_tree(root)
    except ValueError as exc:
        raise experiment_package_error(str(exc)) from exc

    manifest = _load_manifest(root / _PACKAGE_MANIFEST_NAME)
    schema_version = _manifest_schema_version(manifest)
    _validate_manifest_schema(manifest, schema_version)

    experiment_id = str(manifest["experiment_id"])
    if _EXPERIMENT_ID_PATTERN.fullmatch(experiment_id) is None:
        raise experiment_package_error(
            "experiment_id must contain only lowercase letters, digits, and underscores."
        )

    contract_path = _resolve_file(root, manifest["contract"], "contract")
    contract, contract_validation = _load_and_validate_contract(
        contract_path,
        contract_schema_path,
    )

    contract_experiment_id = _contract_experiment_id(contract)
    if contract_experiment_id != experiment_id:
        raise experiment_package_error(
            f"Manifest experiment_id '{experiment_id}' does not match Contract experiment.id "
            f"'{contract_experiment_id}'."
        )

    runtime = _load_runtime_component(root, manifest.get("runtime"))
    integrations = _load_integration_components(
        root,
        manifest.get("integrations"),
    )
    analysis = _load_analysis_components(root, manifest.get("analysis"))

    return experiment_package(
        root=root,
        schema_version=schema_version,
        experiment_id=experiment_id,
        contract_path=contract_path,
        contract=MappingProxyType(contract),
        contract_sha256=_file_sha256(contract_path),
        package_sha256=package_tree_sha256(root),
        runtime=runtime,
        integrations=integrations,
        analysis=analysis,
        contract_warnings=contract_validation.warnings,
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as exc:
        raise experiment_package_error(
            f"Could not read Experiment Package manifest '{path}': {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise experiment_package_error(
            f"Could not parse Experiment Package manifest '{path}' as YAML: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise experiment_package_error(
            f"Experiment Package manifest '{path}' must contain a YAML mapping."
        )

    return data


def _manifest_schema_version(manifest: dict[str, Any]) -> str:
    schema_version = manifest.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        raise experiment_package_error(
            "Experiment Package manifest requires a non-empty string schema_version."
        )

    return schema_version


def _validate_manifest_schema(
    manifest: dict[str, Any],
    schema_version: str,
) -> None:
    try:
        schema = load_package_manifest_schema(schema_version)
    except (RuntimeError, ValueError) as exc:
        raise experiment_package_error(str(exc)) from exc

    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(manifest),
        key=lambda error: list(error.absolute_path),
    )

    if not errors:
        return

    error = errors[0]
    path = _format_path(error.absolute_path)
    raise experiment_package_error(
        f"Experiment Package manifest schema violation at '{path}': {error.message}"
    )


def _load_and_validate_contract(
    path: Path,
    schema_path: str | Path | None,
) -> tuple[dict[str, Any], Any]:
    try:
        contract = load_contract(path)
        schema = load_schema_for_contract(contract, schema_path)
    except (contract_load_error, OSError, ValueError) as exc:
        raise experiment_package_error(str(exc)) from exc

    result = validate_contract(contract, schema)
    if result.errors:
        issue = result.errors[0]
        raise experiment_package_error(
            f"Experiment Contract validation failed at '{issue.path}': {issue.message}"
        )

    return contract, result


def _contract_experiment_id(contract: Mapping[str, Any]) -> str:
    experiment = contract.get("experiment")
    if not isinstance(experiment, dict):
        raise experiment_package_error("Experiment Contract requires an experiment mapping.")

    experiment_id = experiment.get("id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise experiment_package_error("Experiment Contract requires experiment.id.")

    return experiment_id


def _load_runtime_component(
    package_root: Path,
    data: Any,
) -> experiment_runtime_component | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise experiment_package_error("runtime must be a mapping.")

    api_version = str(data["api_version"])
    if api_version != EXPERIMENT_RUNTIME_API_VERSION:
        raise experiment_package_error(
            f"Unsupported runtime.api_version '{api_version}'."
        )

    root = _resolve_directory(package_root, data["root"], "runtime.root")
    entrypoint = str(data["entrypoint"])
    if not entrypoint.isidentifier():
        raise experiment_package_error(
            "runtime.entrypoint must be a valid Python identifier."
        )
    if not (root / "__init__.py").is_file():
        raise experiment_package_error(
            f"runtime.root '{root}' must contain '__init__.py'."
        )

    return experiment_runtime_component(
        api_version=api_version,
        root=root,
        entrypoint=entrypoint,
        sha256=component_tree_sha256(root),
    )


def _load_integration_components(
    package_root: Path,
    data: Any,
) -> tuple[experiment_integration_component, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise experiment_package_error("integrations must be a sequence.")

    components: list[experiment_integration_component] = []
    seen_ids: set[str] = set()

    for index, item in enumerate(data):
        field = f"integrations[{index}]"
        if not isinstance(item, dict):
            raise experiment_package_error(f"{field} must be a mapping.")

        integration_id = str(item["id"])
        if _COMPONENT_ID_PATTERN.fullmatch(integration_id) is None:
            raise experiment_package_error(
                f"Invalid integration id '{integration_id}'."
            )
        if integration_id in seen_ids:
            raise experiment_package_error(
                f"Duplicate integration id '{integration_id}'."
            )
        seen_ids.add(integration_id)

        api_version = str(item["api_version"])
        if api_version != EXPERIMENT_INTEGRATION_API_VERSION:
            raise experiment_package_error(
                f"Unsupported {field}.api_version '{api_version}'."
            )

        root = _resolve_directory(package_root, item["root"], f"{field}.root")
        if not (root / "__init__.py").is_file():
            raise experiment_package_error(
                f"{field}.root '{root}' must contain '__init__.py'."
            )

        entrypoint = str(item["entrypoint"])
        if not entrypoint.isidentifier():
            raise experiment_package_error(
                f"{field}.entrypoint must be a valid Python identifier."
            )

        preset_value = item.get("preset")
        preset = None if preset_value is None else str(preset_value)
        if preset is not None and not preset.strip():
            raise experiment_package_error(f"{field}.preset must not be empty.")

        configuration_value = item.get("configuration", {})
        if not isinstance(configuration_value, dict):
            raise experiment_package_error(
                f"{field}.configuration must be a mapping."
            )

        secret_values = item.get("secrets", [])
        if not isinstance(secret_values, list):
            raise experiment_package_error(f"{field}.secrets must be a sequence.")
        secrets: list[experiment_integration_secret] = []
        seen_secret_ids: set[str] = set()
        for secret_index, secret_item in enumerate(secret_values):
            secret_field = f"{field}.secrets[{secret_index}]"
            if not isinstance(secret_item, dict):
                raise experiment_package_error(f"{secret_field} must be a mapping.")
            secret_id = str(secret_item["id"])
            if _COMPONENT_ID_PATTERN.fullmatch(secret_id) is None:
                raise experiment_package_error(
                    f"Invalid integration secret id '{secret_id}'."
                )
            if secret_id in seen_secret_ids:
                raise experiment_package_error(
                    f"Duplicate integration secret id '{secret_id}'."
                )
            seen_secret_ids.add(secret_id)
            required = secret_item.get("required", True)
            if not isinstance(required, bool):
                raise experiment_package_error(
                    f"{secret_field}.required must be a boolean."
                )
            secrets.append(
                experiment_integration_secret(
                    secret_id=secret_id,
                    required=required,
                )
            )

        components.append(
            experiment_integration_component(
                integration_id=integration_id,
                api_version=api_version,
                root=root,
                entrypoint=entrypoint,
                preset=preset,
                configuration=_freeze_mapping(configuration_value),
                secrets=tuple(secrets),
                sha256=component_tree_sha256(root),
            )
        )

    return tuple(components)

def _load_analysis_components(
    package_root: Path,
    data: Any,
) -> tuple[experiment_analysis_component, ...]:
    if data is None:
        return ()
    if not isinstance(data, dict):
        raise experiment_package_error("analysis must be a mapping.")

    components: list[experiment_analysis_component] = []
    for index, item in enumerate(data["supplementary"]):
        kind = str(item["kind"])
        if _COMPONENT_ID_PATTERN.fullmatch(kind) is None:
            raise experiment_package_error(
                f"analysis.supplementary[{index}].kind is invalid: '{kind}'."
            )

        field = f"analysis.supplementary[{index}].configuration"
        configuration = _resolve_file(package_root, item["configuration"], field)
        components.append(
            experiment_analysis_component(
                kind=kind,
                configuration=configuration,
                sha256=_file_sha256(configuration),
            )
        )

    return tuple(components)


def _resolve_file(package_root: Path, value: Any, field: str) -> Path:
    path = _resolve_package_path(package_root, value, field)
    if not path.is_file():
        raise experiment_package_error(f"{field} does not identify a file: '{path}'.")
    return path


def _resolve_directory(package_root: Path, value: Any, field: str) -> Path:
    path = _resolve_package_path(package_root, value, field)
    if not path.is_dir():
        raise experiment_package_error(
            f"{field} does not identify a directory: '{path}'."
        )
    return path


def _resolve_package_path(package_root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise experiment_package_error(f"{field} must be a non-empty string path.")

    relative = Path(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise experiment_package_error(f"{field} must be a safe package-relative path.")

    source_path = package_root / relative
    if not is_managed_package_path(source_path, package_root):
        raise experiment_package_error(
            f"{field} must identify managed Experiment Package content."
        )

    resolved = source_path.resolve()
    try:
        resolved.relative_to(package_root)
    except ValueError as exc:
        raise experiment_package_error(
            f"{field} resolves outside the Experiment Package root."
        ) from exc

    return resolved


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()



def _freeze_mapping(value: dict[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(
        {key: _freeze_value(item) for key, item in value.items()}
    )


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value

def _format_path(path: Any) -> str:
    components: list[str] = []

    for part in path:
        if isinstance(part, int):
            if components:
                components[-1] += f"[{part}]"
            else:
                components.append(f"[{part}]")
        else:
            components.append(str(part))

    return ".".join(components) if components else "$"
