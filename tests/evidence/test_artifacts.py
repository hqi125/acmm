from __future__ import annotations

import json

import pytest

from acmm.evidence.artifacts import (
    find_evidence,
    load_conversation_messages,
    load_json_object_array_evidence,
    load_text_evidence,
)
from acmm.runtime.interfaces import captured_evidence


def _artifact(
    evidence_id: str,
    content: bytes,
) -> captured_evidence:
    return captured_evidence(
        id=evidence_id,
        channel="test",
        artifact=f"evidence/{evidence_id}",
        content=content,
    )


def test_load_text_evidence_preserves_original_unicode() -> None:
    text = "结果为 ١٠٠ → complete"
    evidence = (_artifact("response", text.encode("utf-8")),)

    assert load_text_evidence(evidence, "response") == text


def test_load_json_object_array_evidence_requires_object_array() -> None:
    valid = (_artifact("items", json.dumps([{"id": 1}]).encode("utf-8")),)
    invalid = (_artifact("items", json.dumps([1, 2]).encode("utf-8")),)

    assert load_json_object_array_evidence(valid, "items") == [{"id": 1}]

    with pytest.raises(ValueError, match="JSON array of objects"):
        load_json_object_array_evidence(invalid, "items")


def test_load_conversation_messages_reads_message_objects() -> None:
    conversation = {
        "messages": [
            {
                "role": "assistant",
                "content": "hello",
            }
        ]
    }
    evidence = (
        _artifact(
            "conversation",
            json.dumps(conversation).encode("utf-8"),
        ),
    )

    assert load_conversation_messages(evidence) == conversation["messages"]


def test_find_evidence_reports_missing_artifact() -> None:
    with pytest.raises(ValueError, match="Required evidence 'missing' is missing"):
        find_evidence((), "missing")
