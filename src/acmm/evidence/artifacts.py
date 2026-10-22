from __future__ import annotations

"""Typed access to persisted evidence artifacts."""

import json
from typing import Any

from acmm.runtime.interfaces import captured_evidence


def find_evidence(
    evidence: tuple[captured_evidence, ...],
    evidence_id: str,
) -> captured_evidence:
    for artifact in evidence:
        if artifact.id == evidence_id:
            return artifact

    raise ValueError(f"Required evidence '{evidence_id}' is missing.")


def load_text_evidence(
    evidence: tuple[captured_evidence, ...],
    evidence_id: str,
) -> str:
    artifact = find_evidence(evidence, evidence_id)

    try:
        return artifact.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Evidence '{evidence_id}' is not valid UTF-8 text."
        ) from exc


def load_json_object_array_evidence(
    evidence: tuple[captured_evidence, ...],
    evidence_id: str,
) -> list[dict[str, Any]]:
    artifact = find_evidence(evidence, evidence_id)

    try:
        value = json.loads(artifact.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Evidence '{evidence_id}' is not valid UTF-8 JSON."
        ) from exc

    if not isinstance(value, list) or not all(
        isinstance(item, dict) for item in value
    ):
        raise ValueError(
            f"Evidence '{evidence_id}' must contain a JSON array of objects."
        )

    return value


def load_conversation_messages(
    evidence: tuple[captured_evidence, ...],
    evidence_id: str = "conversation",
) -> list[dict[str, Any]]:
    artifact = find_evidence(evidence, evidence_id)

    try:
        value = json.loads(artifact.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Evidence '{evidence_id}' is not valid UTF-8 JSON."
        ) from exc

    if not isinstance(value, dict) or not isinstance(value.get("messages"), list):
        raise ValueError(
            f"Evidence '{evidence_id}' must contain a messages array."
        )

    messages = value["messages"]

    if not all(isinstance(item, dict) for item in messages):
        raise ValueError(
            f"Evidence '{evidence_id}' messages must be JSON objects."
        )

    return messages
