from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Iterable, Literal

from acmm.experiment_package.provenance import experiment_deployment_provenance
from acmm_infra.common import infrastructure_error


def install_default_if_missing(source: Path, destination: Path) -> str:
    if not source.is_file():
        raise infrastructure_error(
            f"Shipped infrastructure default does not exist: '{source}'."
        )

    if destination.exists():
        return "preserved"

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return "created"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        values[key.strip()] = value.strip()

    return values


def write_env(
    path: Path,
    values: dict[str, str],
    *,
    header_lines: Iterable[str],
    line_ending: str = "\n",
) -> None:
    lines = [*header_lines, ""]
    lines.extend(f"{key}={values[key]}" for key in sorted(values))
    content = line_ending.join(lines) + line_ending
    path.write_bytes(content.encode("utf-8"))


def files_equal(left: Path, right: Path) -> bool:
    return (
        left.is_file()
        and right.is_file()
        and left.read_bytes() == right.read_bytes()
    )


def bool_text(value: bool) -> str:
    return "true" if value else "false"


LLM_BASELINE_STATE_FILE = ".acmm-llm-baseline.json"
LLM_BASELINE_STATE_SCHEMA_VERSION = "0.1"


def read_llm_baseline_state(deployment_root: Path) -> dict[str, object] | None:
    path = deployment_root / LLM_BASELINE_STATE_FILE
    if not path.is_file():
        return None

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise infrastructure_error(
            f"Could not read LLM baseline state '{path}': {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise infrastructure_error(
            f"LLM baseline state '{path}' has an unsupported format."
        )
    if value.get("schema_version") != LLM_BASELINE_STATE_SCHEMA_VERSION:
        raise infrastructure_error(
            f"LLM baseline state '{path}' has an unsupported format."
        )
    baseline_id = value.get("llm_baseline_id")
    if not isinstance(baseline_id, str) or not baseline_id.strip():
        raise infrastructure_error(
            f"LLM baseline state '{path}' has an invalid baseline ID."
        )
    return value


def write_llm_baseline_state(
    deployment_root: Path,
    *,
    llm_baseline_id: str,
    model_name: str,
    logical_provider: str,
) -> None:
    path = deployment_root / LLM_BASELINE_STATE_FILE
    payload = {
        "schema_version": LLM_BASELINE_STATE_SCHEMA_VERSION,
        "llm_baseline_id": llm_baseline_id,
        "model_name": model_name,
        "logical_provider": logical_provider,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)
EXPERIMENT_APPARATUS_STATE_FILE = ".acmm-experiment-apparatus.json"
experiment_application_scope = Literal["experiment", "tools"]
EXPERIMENT_APPARATUS_STATE_SCHEMA_VERSION = "0.3"


def read_experiment_apparatus_state(deployment_root: Path) -> dict[str, object] | None:
    path = deployment_root / EXPERIMENT_APPARATUS_STATE_FILE
    if not path.is_file():
        return None

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise infrastructure_error(
            f"Could not read experiment apparatus state '{path}': {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise infrastructure_error(
            f"Experiment apparatus state '{path}' has an unsupported format."
        )

    schema_version = value.get("schema_version")
    if schema_version == "0.1":
        value = dict(value)
        value["application_scope"] = "experiment"
        value["package"] = None
        return value

    if schema_version == "0.2":
        value = dict(value)
        value["package"] = None
    elif schema_version == EXPERIMENT_APPARATUS_STATE_SCHEMA_VERSION:
        value = dict(value)
    else:
        raise infrastructure_error(
            f"Experiment apparatus state '{path}' has an unsupported format."
        )

    application_scope = value.get("application_scope")
    if application_scope not in {"experiment", "tools"}:
        raise infrastructure_error(
            f"Experiment apparatus state '{path}' has an invalid application scope."
        )

    package = value.get("package")
    if package is not None:
        _validate_experiment_package_state(path, value, package)
    return value


def active_experiment_id(deployment_root: Path) -> str | None:
    state = read_experiment_apparatus_state(deployment_root)
    if state is None:
        return None

    experiment_id = state.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        path = deployment_root / EXPERIMENT_APPARATUS_STATE_FILE
        raise infrastructure_error(
            f"Experiment apparatus state '{path}' has an invalid experiment ID."
        )

    return experiment_id


def write_experiment_apparatus_state(
    deployment_root: Path,
    *,
    experiment_id: str,
    source_sha256: str | None,
    application_scope: experiment_application_scope,
    resources: list[dict[str, str]],
    package_provenance: experiment_deployment_provenance | None = None,
) -> None:
    path = deployment_root / EXPERIMENT_APPARATUS_STATE_FILE
    payload: dict[str, object] = {
        "schema_version": EXPERIMENT_APPARATUS_STATE_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "source_sha256": source_sha256,
        "application_scope": application_scope,
        "resources": resources,
    }
    if package_provenance is not None:
        _validate_write_package_provenance(
            experiment_id,
            source_sha256,
            package_provenance,
        )
        package = package_provenance.package
        payload["package"] = {
            "schema_version": package.package_schema_version,
            "sha256": package.package_sha256,
            "contract_sha256": package.contract_sha256,
            "runtime_sha256": package.runtime_sha256,
            "apparatus_subject": package_provenance.infrastructure_subject,
            "apparatus_sha256": package_provenance.apparatus_sha256,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def clear_experiment_apparatus_state(deployment_root: Path) -> None:
    path = deployment_root / EXPERIMENT_APPARATUS_STATE_FILE
    if path.exists():
        path.unlink()


def _validate_experiment_package_state(
    path: Path,
    state: dict[str, object],
    package: object,
) -> None:
    if not isinstance(package, dict):
        raise infrastructure_error(
            f"Experiment apparatus state '{path}' has invalid package provenance."
        )

    experiment_id = state.get("experiment_id")
    source_sha256 = state.get("source_sha256")
    required_strings = (
        "schema_version",
        "sha256",
        "contract_sha256",
        "apparatus_subject",
    )
    for field in required_strings:
        value = package.get(field)
        if not isinstance(value, str) or not value.strip():
            raise infrastructure_error(
                f"Experiment apparatus state '{path}' has invalid package "
                f"provenance field '{field}'."
            )

    for field in ("sha256", "contract_sha256"):
        value = package[field]
        assert isinstance(value, str)
        if not _is_sha256(value):
            raise infrastructure_error(
                f"Experiment apparatus state '{path}' has invalid package "
                f"SHA-256 field '{field}'."
            )

    apparatus_sha256 = package.get("apparatus_sha256")
    if apparatus_sha256 is not None and (
        not isinstance(apparatus_sha256, str) or not _is_sha256(apparatus_sha256)
    ):
        raise infrastructure_error(
            f"Experiment apparatus state '{path}' has invalid package "
            "apparatus_sha256."
        )

    runtime_sha256 = package.get("runtime_sha256")
    if runtime_sha256 is not None and (
        not isinstance(runtime_sha256, str) or not _is_sha256(runtime_sha256)
    ):
        raise infrastructure_error(
            f"Experiment apparatus state '{path}' has invalid package "
            "runtime_sha256."
        )

    if source_sha256 != apparatus_sha256:
        raise infrastructure_error(
            f"Experiment apparatus state '{path}' package apparatus SHA-256 "
            "does not match source_sha256."
        )
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise infrastructure_error(
            f"Experiment apparatus state '{path}' has an invalid experiment ID."
        )


def _validate_write_package_provenance(
    experiment_id: str,
    source_sha256: str | None,
    provenance: experiment_deployment_provenance,
) -> None:
    if provenance.package.experiment_id != experiment_id:
        raise infrastructure_error(
            "Experiment Package provenance experiment ID does not match the active "
            "experiment."
        )
    if provenance.apparatus_sha256 != source_sha256:
        raise infrastructure_error(
            "Experiment Package apparatus provenance does not match source_sha256."
        )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef"
        for character in value
    )
