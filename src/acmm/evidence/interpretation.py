from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from acmm.evidence.semantic import (
    interpret_semantic_scalar,
    output_scalar_semantics_for_path,
)
from acmm.evidence.structured import decode_json_object_text


@dataclass(frozen=True)
class semantic_scalar_normalization:
    path: str
    semantic_type: str
    source_serialization: str


@dataclass(frozen=True)
class interpreted_structured_output:
    evidence_id: str
    root: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    syntax_normalizations: tuple[str, ...] = ()
    semantic_normalizations: tuple[semantic_scalar_normalization, ...] = ()
    representation_status: str = "unreadable"
    representation_conforming: bool | None = None
    error: str | None = None


def interpret_scenario_evidence(
    scenario: Mapping[str, Any],
    evidence: tuple[Any, ...],
) -> dict[str, interpreted_structured_output]:
    """Build Contract-directed interpreted evidence views for one scenario.

    Raw evidence remains immutable.  This function only derives an in-memory structured view from
    declarations under ``task.output_semantics``.  Syntax recovery is Core-owned; semantic scalar
    representation is applied only where the Contract explicitly declares it.
    """

    task = scenario.get("task")
    if not isinstance(task, Mapping):
        return {}
    output_semantics = task.get("output_semantics")
    if not isinstance(output_semantics, Mapping):
        return {}
    structured = output_semantics.get("structured_object")
    if not isinstance(structured, Mapping):
        return {}

    evidence_id = structured.get("evidence_id")
    payload_path = structured.get("payload_path")
    required_payload_keys = structured.get("required_payload_keys")
    accepted_presentations = structured.get("accepted_presentations")
    if (
        not isinstance(evidence_id, str)
        or not isinstance(payload_path, str)
        or not isinstance(required_payload_keys, list)
    ):
        raise ValueError("Validated structured output semantics are malformed.")

    artifact = next((item for item in evidence if getattr(item, "id", None) == evidence_id), None)
    if artifact is None:
        return {
            evidence_id: interpreted_structured_output(
                evidence_id=evidence_id,
                representation_status="unreadable",
                representation_conforming=(False if accepted_presentations else None),
                error=f"Required evidence '{evidence_id}' is missing.",
            )
        }
    try:
        text = artifact.content.decode("utf-8")
    except UnicodeDecodeError:
        return {
            evidence_id: interpreted_structured_output(
                evidence_id=evidence_id,
                representation_status="unreadable",
                representation_conforming=(False if accepted_presentations else None),
                error=f"Evidence '{evidence_id}' is not valid UTF-8 text.",
            )
        }

    view = _interpret_structured_text(
        scenario,
        evidence_id,
        text,
        payload_path,
        tuple(str(key) for key in required_payload_keys),
        tuple(str(item) for item in accepted_presentations)
        if isinstance(accepted_presentations, list)
        else (),
    )
    return {evidence_id: view}


def interpretation_detail(
    view: interpreted_structured_output,
) -> str | None:
    parts: list[str] = []
    if view.syntax_normalizations:
        parts.append(
            "syntax=" + ",".join(view.syntax_normalizations)
        )
    if view.semantic_normalizations:
        semantic = ",".join(
            f"{item.path}:{item.source_serialization}->{item.semantic_type}"
            for item in view.semantic_normalizations
        )
        parts.append("semantics=" + semantic)
    if not parts:
        return None
    return "Evidence interpretation: " + "; ".join(parts) + "."


def _interpret_structured_text(
    scenario: Mapping[str, Any],
    evidence_id: str,
    text: str,
    payload_path: str,
    required_payload_keys: tuple[str, ...],
    accepted_presentations: tuple[str, ...],
) -> interpreted_structured_output:
    segments = tuple(payload_path.split("."))
    root = None
    syntax: tuple[str, ...] = ()

    outer = decode_json_object_text(text, required_keys=(segments[0],))
    if outer is not None:
        candidate_root = outer.value
        payload = _value_at_path(candidate_root, segments)
        if isinstance(payload, dict) and _has_required_keys(payload, required_payload_keys):
            root = candidate_root
            syntax = outer.normalizations
        elif isinstance(payload, str):
            nested = decode_json_object_text(payload, required_keys=required_payload_keys)
            if nested is not None:
                root = deepcopy(candidate_root)
                _set_value_at_path(root, segments, nested.value)
                syntax = (
                    *outer.normalizations,
                    "payload_string_unwrap",
                    *nested.normalizations,
                )

    if root is None:
        payload = decode_json_object_text(text, required_keys=required_payload_keys)
        if payload is not None:
            root = _wrap_payload(segments, payload.value)
            syntax = (
                *payload.normalizations,
                "payload_envelope_reconstruction",
            )

    if root is None:
        return interpreted_structured_output(
            evidence_id=evidence_id,
            representation_status="unreadable",
            representation_conforming=(False if accepted_presentations else None),
            error="The evidence did not contain a readable Contract-declared structured payload.",
        )

    semantic_root, semantic_normalizations = _apply_scalar_semantics(scenario, root)
    payload = _value_at_path(semantic_root, segments)
    if not isinstance(payload, dict) or not _has_required_keys(payload, required_payload_keys):
        return interpreted_structured_output(
            evidence_id=evidence_id,
            error="The interpreted structured payload does not satisfy the Contract declaration.",
            syntax_normalizations=syntax,
            semantic_normalizations=semantic_normalizations,
            representation_status=_representation_status(syntax),
            representation_conforming=_representation_conforming(
                syntax, accepted_presentations
            ),
        )

    return interpreted_structured_output(
        evidence_id=evidence_id,
        root=semantic_root,
        payload=payload,
        syntax_normalizations=syntax,
        semantic_normalizations=semantic_normalizations,
        representation_status=_representation_status(syntax),
        representation_conforming=_representation_conforming(
            syntax, accepted_presentations
        ),
    )


def _representation_status(syntax_normalizations: tuple[str, ...]) -> str:
    return "exact_json_object" if not syntax_normalizations else "recovered_json_object"


def _representation_conforming(
    syntax_normalizations: tuple[str, ...],
    accepted_presentations: tuple[str, ...],
) -> bool | None:
    if not accepted_presentations:
        return None
    source = _representation_status(syntax_normalizations)
    return source in accepted_presentations


def _apply_scalar_semantics(
    scenario: Mapping[str, Any],
    root: dict[str, Any],
) -> tuple[dict[str, Any], tuple[semantic_scalar_normalization, ...]]:
    task = scenario.get("task")
    output_semantics = task.get("output_semantics") if isinstance(task, Mapping) else None
    fields = output_semantics.get("scalar_fields") if isinstance(output_semantics, Mapping) else None
    if not isinstance(fields, list) or not fields:
        return root, ()

    interpreted = deepcopy(root)
    normalizations: list[semantic_scalar_normalization] = []
    for item in fields:
        if not isinstance(item, Mapping):
            continue
        path = item.get("path")
        if not isinstance(path, str):
            continue
        semantics = output_scalar_semantics_for_path(scenario, path)
        if semantics is None:
            continue
        segments = tuple(path.split("."))
        present, raw_value = _try_value_at_path(interpreted, segments)
        if not present:
            continue
        scalar = interpret_semantic_scalar(raw_value, semantics)
        if not scalar.accepted:
            continue
        if scalar.source_serialization == "canonical_string":
            _set_value_at_path(interpreted, segments, scalar.value)
            normalizations.append(
                semantic_scalar_normalization(
                    path=path,
                    semantic_type=semantics.semantic_type,
                    source_serialization=scalar.source_serialization,
                )
            )

    return interpreted, tuple(normalizations)


def _has_required_keys(value: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    return all(key in value for key in keys)


def _value_at_path(root: Mapping[str, Any], segments: tuple[str, ...]) -> Any:
    current: Any = root
    for segment in segments:
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _try_value_at_path(
    root: Mapping[str, Any],
    segments: tuple[str, ...],
) -> tuple[bool, Any]:
    current: Any = root
    for segment in segments:
        if not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _set_value_at_path(root: dict[str, Any], segments: tuple[str, ...], value: Any) -> None:
    current = root
    for segment in segments[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            child = {}
            current[segment] = child
        current = child
    current[segments[-1]] = value


def _wrap_payload(segments: tuple[str, ...], payload: dict[str, Any]) -> dict[str, Any]:
    root: dict[str, Any] = {}
    _set_value_at_path(root, segments, payload)
    return root
