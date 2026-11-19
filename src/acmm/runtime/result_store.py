from __future__ import annotations

from contextlib import nullcontext
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

import yaml

from acmm.runtime.interfaces import captured_evidence, executed_run
from acmm.runtime.interruption import protect_interruption_cleanup


class result_write_error(RuntimeError):
    """Raised when an executed run cannot be persisted safely."""


def persist_executed_run(
    result: executed_run,
    results_root: str | Path,
) -> Path:
    """
    Persist one executed run under ``results_root/<experiment_id>/``.

    This convenience entry point preserves the original standalone result
    layout. Experiment-level execution uses ``persist_executed_run_to()`` to
    supply an already established per-session runs directory.
    """

    experiment_id = result.record["run"]["experiment_id"]
    _validate_directory_component("experiment ID", experiment_id)

    experiment_dir = Path(results_root) / experiment_id
    return persist_executed_run_to(result, experiment_dir)


def persist_executed_run_to(
    result: executed_run,
    destination: str | Path,
) -> Path:
    """Persist one executed run directly under ``destination``."""

    record = result.record
    run = record["run"]

    run_index = run["run_index"]
    run_id = run["id"]

    _validate_directory_component("run ID", run_id)

    if (
        not isinstance(run_index, int)
        or isinstance(run_index, bool)
        or run_index < 0
    ):
        raise result_write_error(
            "Run index must be a non-negative integer."
        )

    experiment_dir = Path(destination)
    experiment_dir.mkdir(parents=True, exist_ok=True)

    directory_name = f"run_{run_index:06d}_{run_id}"
    final_dir = experiment_dir / directory_name

    if final_dir.exists():
        raise result_write_error(
            f"Result directory already exists: '{final_dir}'."
        )

    artifacts_by_id = _validate_bundle_consistency(result)

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{directory_name}.tmp-",
            dir=experiment_dir,
        )
    )

    try:
        for manifest_entry in _artifact_manifest_entries(record):
            artifact = artifacts_by_id[manifest_entry["id"]]
            artifact_path = _artifact_destination(
                temp_dir,
                manifest_entry["artifact"],
            )

            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            _write_bytes(artifact_path, artifact.content)

        record_path = temp_dir / "run_record.yaml"
        record_text = yaml.safe_dump(
            record,
            sort_keys=False,
            allow_unicode=False,
        )
        _write_bytes(
            record_path,
            record_text.encode("utf-8"),
        )

        os.rename(temp_dir, final_dir)
    except BaseException as exc:
        cleanup_scope = (
            protect_interruption_cleanup()
            if isinstance(exc, KeyboardInterrupt)
            else nullcontext()
        )
        with cleanup_scope:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

        if isinstance(exc, KeyboardInterrupt):
            raise
        if isinstance(exc, result_write_error):
            raise

        raise result_write_error(
            f"Could not persist run '{run_id}': {exc}"
        ) from exc

    return final_dir


def _validate_bundle_consistency(
    result: executed_run,
) -> dict[str, captured_evidence]:
    manifest = _artifact_manifest_entries(result.record)

    manifest_by_id: dict[str, dict] = {}
    manifest_paths: set[str] = set()

    for entry in manifest:
        evidence_id = entry["id"]
        artifact_path = entry["artifact"]

        if evidence_id in manifest_by_id:
            raise result_write_error(
                f"Run Record contains duplicate evidence ID '{evidence_id}'."
            )

        if artifact_path in manifest_paths:
            raise result_write_error(
                f"Run Record contains duplicate artifact path '{artifact_path}'."
            )

        _validate_artifact_path(artifact_path)

        manifest_by_id[evidence_id] = entry
        manifest_paths.add(artifact_path)

    artifacts_by_id: dict[str, captured_evidence] = {}

    for artifact in result.artifacts:
        if artifact.id in artifacts_by_id:
            raise result_write_error(
                f"Executed run contains duplicate evidence ID '{artifact.id}'."
            )

        artifacts_by_id[artifact.id] = artifact

    manifest_ids = set(manifest_by_id)
    artifact_ids = set(artifacts_by_id)

    missing_ids = sorted(manifest_ids - artifact_ids)

    if missing_ids:
        raise result_write_error(
            "Executed run is missing evidence artifact(s): "
            + ", ".join(missing_ids)
            + "."
        )

    extra_ids = sorted(artifact_ids - manifest_ids)

    if extra_ids:
        raise result_write_error(
            "Executed run contains undeclared evidence artifact(s): "
            + ", ".join(extra_ids)
            + "."
        )

    for evidence_id, entry in manifest_by_id.items():
        artifact = artifacts_by_id[evidence_id]

        expected_channel = entry.get("channel", "diagnostic")
        if artifact.channel != expected_channel:
            raise result_write_error(
                f"Artifact '{evidence_id}' channel does not match the Run Record."
            )

        if artifact.artifact != entry["artifact"]:
            raise result_write_error(
                f"Evidence '{evidence_id}' path does not match the Run Record."
            )

        digest = hashlib.sha256(artifact.content).hexdigest()

        if digest != entry["sha256"]:
            raise result_write_error(
                f"Evidence '{evidence_id}' SHA-256 does not match the Run Record."
            )

    return artifacts_by_id


def _artifact_manifest_entries(record: dict) -> list[dict]:
    entries = list(record.get("evidence", ()))
    entries.extend(record.get("diagnostics", ()))
    return entries


def _artifact_destination(
    temp_dir: Path,
    artifact: str,
) -> Path:
    _validate_artifact_path(artifact)

    parts = artifact.split("/")
    destination = temp_dir.joinpath(*parts)

    temp_root = temp_dir.resolve()
    resolved_parent = destination.parent.resolve()

    if not resolved_parent.is_relative_to(temp_root):
        raise result_write_error(
            f"Evidence artifact path escapes the result directory: '{artifact}'."
        )

    return destination


def _validate_directory_component(
    label: str,
    value: object,
) -> None:
    if not isinstance(value, str) or not value:
        raise result_write_error(
            f"{label.capitalize()} must be a non-empty string."
        )

    if value in {".", ".."} or "/" in value or "\\" in value or ":" in value:
        raise result_write_error(
            f"{label.capitalize()} is not safe for a result-directory name: "
            f"'{value}'."
        )


def _validate_artifact_path(artifact: str) -> None:
    if not artifact:
        raise result_write_error("Evidence artifact path cannot be empty.")

    if "\\" in artifact:
        raise result_write_error(
            f"Evidence artifact path must use '/' separators: '{artifact}'."
        )

    if artifact.startswith("/"):
        raise result_write_error(
            f"Evidence artifact path must be relative: '{artifact}'."
        )

    parts = artifact.split("/")

    if any(part in {"", ".", ".."} for part in parts):
        raise result_write_error(
            f"Evidence artifact path contains an unsafe segment: '{artifact}'."
        )

    if ":" in parts[0]:
        raise result_write_error(
            f"Evidence artifact path cannot use a drive prefix: '{artifact}'."
        )


def _write_bytes(
    path: Path,
    content: bytes,
) -> None:
    with path.open("wb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
