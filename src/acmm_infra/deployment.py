from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Iterable

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
EXPERIMENT_PACKAGE_STATE_FILE = ".acmm-experiment-package.json"
EXPERIMENT_PACKAGE_STATE_SCHEMA_VERSION = "0.1"


def read_experiment_package_state(deployment_root: Path) -> dict[str, object] | None:
    path = deployment_root / EXPERIMENT_PACKAGE_STATE_FILE
    if not path.is_file():
        return None

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise infrastructure_error(
            f"Could not read experiment package state '{path}': {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise infrastructure_error(
            f"Experiment package state '{path}' has an unsupported format."
        )
    if value.get("schema_version") != EXPERIMENT_PACKAGE_STATE_SCHEMA_VERSION:
        raise infrastructure_error(
            f"Experiment package state '{path}' has an unsupported format."
        )

    experiment_id = value.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise infrastructure_error(
            f"Experiment package state '{path}' has an invalid experiment ID."
        )

    package = value.get("package")
    _validate_experiment_package_state(path, package)
    return value


def active_experiment_id(deployment_root: Path) -> str | None:
    state = read_experiment_package_state(deployment_root)
    if state is None:
        return None
    value = state["experiment_id"]
    assert isinstance(value, str)
    return value


def write_experiment_package_state(
    deployment_root: Path,
    *,
    experiment_id: str,
    resources: list[dict[str, str]],
    package_provenance: experiment_deployment_provenance,
) -> None:
    if package_provenance.package.experiment_id != experiment_id:
        raise infrastructure_error(
            "Experiment Package provenance experiment ID does not match the active "
            "experiment."
        )

    path = deployment_root / EXPERIMENT_PACKAGE_STATE_FILE
    payload: dict[str, object] = {
        "schema_version": EXPERIMENT_PACKAGE_STATE_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "resources": resources,
        "package": {
            "schema_version": package_provenance.package.package_schema_version,
            "sha256": package_provenance.package.package_sha256,
            "contract_sha256": package_provenance.package.contract_sha256,
            "runtime_sha256": package_provenance.package.runtime_sha256,
            "subject": package_provenance.infrastructure_subject,
            "integrations": [
                {
                    "id": item.integration_id,
                    "sha256": item.sha256,
                }
                for item in package_provenance.integrations
            ],
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def clear_experiment_package_state(deployment_root: Path) -> None:
    path = deployment_root / EXPERIMENT_PACKAGE_STATE_FILE
    if path.exists():
        path.unlink()


def _validate_experiment_package_state(path: Path, package: object) -> None:
    if not isinstance(package, dict):
        raise infrastructure_error(
            f"Experiment package state '{path}' has invalid package provenance."
        )

    required_strings = (
        "schema_version",
        "sha256",
        "contract_sha256",
        "subject",
    )
    for field in required_strings:
        value = package.get(field)
        if not isinstance(value, str) or not value.strip():
            raise infrastructure_error(
                f"Experiment package state '{path}' has invalid package provenance "
                f"field '{field}'."
            )

    for field in ("sha256", "contract_sha256"):
        value = package[field]
        assert isinstance(value, str)
        if not _is_sha256(value):
            raise infrastructure_error(
                f"Experiment package state '{path}' has invalid package SHA-256 "
                f"field '{field}'."
            )

    runtime_sha256 = package.get("runtime_sha256")
    if runtime_sha256 is not None and (
        not isinstance(runtime_sha256, str) or not _is_sha256(runtime_sha256)
    ):
        raise infrastructure_error(
            f"Experiment package state '{path}' has invalid runtime SHA-256."
        )

    integrations = package.get("integrations")
    if not isinstance(integrations, list):
        raise infrastructure_error(
            f"Experiment package state '{path}' has invalid integration provenance."
        )

    seen: set[str] = set()
    for index, item in enumerate(integrations):
        if not isinstance(item, dict):
            raise infrastructure_error(
                f"Experiment package state '{path}' integration {index} is invalid."
            )
        integration_id = item.get("id")
        sha256 = item.get("sha256")
        if (
            not isinstance(integration_id, str)
            or not integration_id.strip()
            or integration_id in seen
        ):
            raise infrastructure_error(
                f"Experiment package state '{path}' integration {index} has an "
                "invalid ID."
            )
        if not isinstance(sha256, str) or not _is_sha256(sha256):
            raise infrastructure_error(
                f"Experiment package state '{path}' integration {index} has an "
                "invalid SHA-256."
            )
        seen.add(integration_id)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef"
        for character in value
    )


def experiment_package_state_matches(
    state: dict[str, object] | None,
    *,
    experiment_id: str | None,
    provenance: experiment_deployment_provenance | None,
) -> tuple[bool, str]:
    if experiment_id is None:
        if state is None:
            return True, "none"
        return False, f"active={state.get('experiment_id')!r}"

    if provenance is None:
        return False, "experiment manager has no package provenance"
    if state is None:
        return False, f"observed=None, expected_id={experiment_id!r}"
    if state.get("experiment_id") != experiment_id:
        return (
            False,
            f"observed_id={state.get('experiment_id')!r}, expected_id={experiment_id!r}",
        )

    package = state.get("package")
    if not isinstance(package, dict):
        return False, "deployment state has no package provenance mapping"

    expected_integrations = [
        {"id": item.integration_id, "sha256": item.sha256}
        for item in provenance.integrations
    ]
    checks = (
        ("schema_version", provenance.package.package_schema_version),
        ("sha256", provenance.package.package_sha256),
        ("contract_sha256", provenance.package.contract_sha256),
        ("runtime_sha256", provenance.package.runtime_sha256),
        ("subject", provenance.infrastructure_subject),
        ("integrations", expected_integrations),
    )
    mismatches = [
        f"{key}={package.get(key)!r} expected {expected!r}"
        for key, expected in checks
        if package.get(key) != expected
    ]
    if mismatches:
        return False, "; ".join(mismatches)
    return True, f"package={provenance.package.package_sha256}"
