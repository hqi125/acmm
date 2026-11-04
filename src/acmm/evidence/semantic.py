from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping


_INTEGER_STRING = re.compile(r"(?:0|-?[1-9][0-9]*)\Z")
_NUMBER_STRING = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z"
)
_SUPPORTED_TYPES = frozenset({"integer", "number", "boolean", "string"})
_SUPPORTED_SERIALIZATIONS = frozenset({"native", "canonical_string"})


@dataclass(frozen=True)
class output_scalar_semantics:
    path: str
    semantic_type: str
    accepted_serializations: tuple[str, ...]


@dataclass(frozen=True)
class interpreted_semantic_scalar:
    value: Any
    accepted: bool
    source_serialization: str | None = None


def output_scalar_semantics_for_path(
    scenario: Mapping[str, Any],
    path: str,
) -> output_scalar_semantics | None:
    """Return one task-declared scalar representation policy by exact object-key path."""

    task = scenario.get("task")
    if not isinstance(task, Mapping):
        return None
    output_semantics = task.get("output_semantics")
    if not isinstance(output_semantics, Mapping):
        return None
    fields = output_semantics.get("scalar_fields")
    if not isinstance(fields, list):
        return None

    for item in fields:
        if not isinstance(item, Mapping) or item.get("path") != path:
            continue
        semantic_type = item.get("semantic_type")
        serializations = item.get("accepted_serializations")
        if not isinstance(semantic_type, str) or semantic_type not in _SUPPORTED_TYPES:
            raise ValueError(f"Unsupported output semantic type for '{path}'.")
        if (
            not isinstance(serializations, list)
            or not serializations
            or any(
                not isinstance(value, str) or value not in _SUPPORTED_SERIALIZATIONS
                for value in serializations
            )
        ):
            raise ValueError(f"Unsupported output serialization policy for '{path}'.")
        return output_scalar_semantics(
            path=path,
            semantic_type=semantic_type,
            accepted_serializations=tuple(serializations),
        )

    return None



def interpret_semantic_scalar(
    value: Any,
    semantics: output_scalar_semantics,
) -> interpreted_semantic_scalar:
    """Interpret one returned scalar under an explicit Contract representation policy."""

    if not isinstance(semantics, output_scalar_semantics):
        raise TypeError("semantics must be output_scalar_semantics.")

    if "native" in semantics.accepted_serializations and _native_value_matches_type(
        value,
        semantics.semantic_type,
    ):
        return interpreted_semantic_scalar(
            value=value,
            accepted=True,
            source_serialization="native",
        )

    if "canonical_string" in semantics.accepted_serializations and isinstance(value, str):
        parsed = _parse_canonical_string(value, semantics.semantic_type)
        if parsed is not _UNACCEPTED:
            return interpreted_semantic_scalar(
                value=parsed,
                accepted=True,
                source_serialization="canonical_string",
            )

    return interpreted_semantic_scalar(value=value, accepted=False)


def semantic_scalar_equal(
    returned: Any,
    expected: Any,
    semantics: output_scalar_semantics,
) -> bool:
    """Compare scalar values under one explicit Contract representation policy.

    This helper never guesses a type or serialization policy.  The caller must supply a policy
    declared by the experiment Contract.  Native and canonical-string representations are tested
    independently; no unrelated coercions are performed.
    """

    if not isinstance(semantics, output_scalar_semantics):
        raise TypeError("semantics must be output_scalar_semantics.")

    interpreted = interpret_semantic_scalar(returned, semantics)
    if not interpreted.accepted:
        return False
    return _native_equal(interpreted.value, expected, semantics.semantic_type)



_UNACCEPTED = object()


def _native_value_matches_type(value: Any, semantic_type: str) -> bool:
    if semantic_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if semantic_type == "number":
        return _is_native_number(value)
    if semantic_type == "boolean":
        return isinstance(value, bool)
    if semantic_type == "string":
        return isinstance(value, str)
    raise ValueError(f"Unsupported semantic scalar type '{semantic_type}'.")


def _parse_canonical_string(value: str, semantic_type: str) -> Any:
    if semantic_type == "integer":
        if not _INTEGER_STRING.fullmatch(value):
            return _UNACCEPTED
        return int(value)
    if semantic_type == "number":
        if not _NUMBER_STRING.fullmatch(value):
            return _UNACCEPTED
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            return _UNACCEPTED
        if not parsed.is_finite():
            return _UNACCEPTED
        if "." not in value and "e" not in value.lower():
            return int(value)
        return float(value)
    if semantic_type == "boolean":
        if value == "true":
            return True
        if value == "false":
            return False
        return _UNACCEPTED
    if semantic_type == "string":
        return _UNACCEPTED
    raise ValueError(f"Unsupported semantic scalar type '{semantic_type}'.")


def _native_equal(returned: Any, expected: Any, semantic_type: str) -> bool:
    if semantic_type == "integer":
        return (
            isinstance(returned, int)
            and not isinstance(returned, bool)
            and isinstance(expected, int)
            and not isinstance(expected, bool)
            and returned == expected
        )
    if semantic_type == "number":
        if not _is_native_number(returned) or not _is_native_number(expected):
            return False
        return _decimal_from_native(returned) == _decimal_from_native(expected)
    if semantic_type == "boolean":
        return isinstance(returned, bool) and isinstance(expected, bool) and returned is expected
    if semantic_type == "string":
        return isinstance(returned, str) and isinstance(expected, str) and returned == expected
    raise ValueError(f"Unsupported semantic scalar type '{semantic_type}'.")


def _canonical_string_equal(returned: str, expected: Any, semantic_type: str) -> bool:
    if semantic_type == "integer":
        if not _INTEGER_STRING.fullmatch(returned):
            return False
        if not isinstance(expected, int) or isinstance(expected, bool):
            return False
        return int(returned) == expected

    if semantic_type == "number":
        if not _NUMBER_STRING.fullmatch(returned) or not _is_native_number(expected):
            return False
        try:
            return Decimal(returned) == _decimal_from_native(expected)
        except InvalidOperation:
            return False

    if semantic_type == "boolean":
        if not isinstance(expected, bool):
            return False
        if returned == "true":
            return expected is True
        if returned == "false":
            return expected is False
        return False

    # A JSON string is already the native representation of semantic string data.  Contract
    # semantic validation rejects canonical_string for this type so this path should not occur.
    if semantic_type == "string":
        return False

    raise ValueError(f"Unsupported semantic scalar type '{semantic_type}'.")


def _is_native_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _decimal_from_native(value: int | float) -> Decimal:
    return Decimal(str(value))
