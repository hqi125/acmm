from __future__ import annotations

import hashlib
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator
import yaml

from acmm.contract.loader import contract_load_error, load_contract
from acmm.contract.validator import load_schema, validate_contract
from acmm.experiment_package.model import (
    experiment_analysis_component,
    experiment_apparatus_component,
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
_SUPPORTED_APPARATUS_SCHEMA_VERSION = "0.1"
_SUPPORTED_RUNTIME_API_VERSION = "0.1"
_EXPERIMENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_COMPONENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class experiment_package_error(ValueError):
    """Raised when an Experiment Package cannot be loaded or validated."""


def load_experiment_package(
    package_root: str | Path,
    *,
    contract_schema_path: str | Path,
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
    apparatus = _load_apparatus_components(
        root,
        experiment_id,
        manifest.get("apparatus"),
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
        apparatus=MappingProxyType(apparatus),
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
    schema_path: str | Path,
) -> tuple[dict[str, Any], Any]:
    try:
        contract = load_contract(path)
        schema = load_schema(schema_path)
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
    if api_version != _SUPPORTED_RUNTIME_API_VERSION:
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


def _load_apparatus_components(
    package_root: Path,
    experiment_id: str,
    data: Any,
) -> dict[str, experiment_apparatus_component]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise experiment_package_error("apparatus must be a mapping.")

    components: dict[str, experiment_apparatus_component] = {}
    for infrastructure_subject, path_value in data.items():
        if _COMPONENT_ID_PATTERN.fullmatch(infrastructure_subject) is None:
            raise experiment_package_error(
                f"Invalid apparatus infrastructure subject '{infrastructure_subject}'."
            )

        field = f"apparatus.{infrastructure_subject}"
        root = _resolve_directory(package_root, path_value, field)
        _validate_apparatus_identity(root, experiment_id, field)
        components[infrastructure_subject] = experiment_apparatus_component(
            infrastructure_subject=infrastructure_subject,
            root=root,
            sha256=component_tree_sha256(root),
        )

    return components


def _validate_apparatus_identity(
    apparatus_root: Path,
    experiment_id: str,
    field: str,
) -> None:
    path = apparatus_root / "apparatus.yaml"
    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as exc:
        raise experiment_package_error(
            f"{field} must contain a readable apparatus.yaml: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise experiment_package_error(
            f"Could not parse {field} apparatus.yaml as YAML: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise experiment_package_error(f"{field} apparatus.yaml must be a mapping.")
    if data.get("schema_version") != _SUPPORTED_APPARATUS_SCHEMA_VERSION:
        raise experiment_package_error(
            f"{field} apparatus schema_version must be '{_SUPPORTED_APPARATUS_SCHEMA_VERSION}'."
        )
    if data.get("experiment_id") != experiment_id:
        raise experiment_package_error(
            f"{field} apparatus experiment_id must be '{experiment_id}'."
        )


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
