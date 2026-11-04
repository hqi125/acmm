from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable


@dataclass(frozen=True)
class json_object_decode_result:
    value: dict[str, Any]
    normalizations: tuple[str, ...] = ()


@dataclass(frozen=True)
class _candidate:
    value: dict[str, Any]
    start: int
    end: int


_DUPLICATE_PROPERTY_QUOTE = re.compile(
    r'([,{]\s*)""([^"\\]+)"\s*:',
)

_NORMALIZATION_COST = {
    "json_string_unwrap": 1,
    "trailing_delimiter_completion": 1,
    "trailing_comma": 1,
    "duplicate_property_quote": 2,
    "quote_deescape": 3,
    "quote_deescape_x2": 6,
    "quote_deescape_x3": 9,
}


def decode_json_object_text(
    text: str,
    *,
    required_keys: Iterable[str] = (),
) -> json_object_decode_result | None:
    """Decode one JSON object from structured model output.

    The decoder is strict about semantic structure but tolerant of a bounded set of presentation
    artifacts commonly produced by LLM/agent response wrappers:

    - surrounding prose or Markdown fences;
    - surplus trailing text/delimiters after a complete object;
    - one JSON-string wrapper around an object;
    - missing trailing object/array delimiters;
    - up to three layers of backslash-escaped JSON quotes;
    - trailing commas immediately before ``]`` or ``}``; and
    - a duplicated opening quote on an object property name.

    Persisted evidence is never changed.  ``normalizations`` records every syntax-only transform
    used to recover the returned object.  The decoder never falls back to an arbitrary child object
    when ``required_keys`` are supplied.
    """

    if not isinstance(text, str):
        raise TypeError("JSON object text must be a string.")

    keys = _normalize_required_keys(required_keys)
    stripped = text.strip()
    if not stripped:
        return None

    direct = _find_candidate(stripped, keys)
    if direct is not None:
        normalization = () if direct.start == 0 and direct.end == len(stripped) else ("embedded_json",)
        return json_object_decode_result(
            value=direct.value,
            normalizations=normalization,
        )

    variants: list[tuple[str, tuple[str, ...]]] = [(stripped, ())]
    seen = {stripped}

    def add_variant(
        value: str,
        normalizations: tuple[str, ...],
    ) -> None:
        if value == stripped or value in seen:
            return
        seen.add(value)
        variants.append((value, normalizations))

    # A valid JSON string containing serialized JSON is a common wrapper artifact and can be
    # unwrapped without guessing any content.
    try:
        wrapped = json.loads(stripped)
    except json.JSONDecodeError:
        wrapped = None
    if isinstance(wrapped, str):
        unwrapped = wrapped.strip()
        if unwrapped:
            add_variant(unwrapped, ("json_string_unwrap",))

    duplicate_fixed = _repair_duplicate_property_quote(stripped)
    if duplicate_fixed != stripped:
        add_variant(duplicate_fixed, ("duplicate_property_quote",))

    trailing_comma_fixed = _remove_trailing_commas(stripped)
    if trailing_comma_fixed != stripped:
        add_variant(trailing_comma_fixed, ("trailing_comma",))

    # Response wrappers can accidentally add more than one presentation-escape layer.  Recover at
    # most three layers, and record the depth explicitly.  The bounded depth prevents this helper
    # from turning into an open-ended recursive decoder.
    quote_variant = stripped
    for depth in range(1, 4):
        if r'\"' not in quote_variant:
            break
        quote_deescaped = quote_variant.replace(r'\"', '"')
        if quote_deescaped == quote_variant:
            break

        mode = "quote_deescape" if depth == 1 else f"quote_deescape_x{depth}"
        add_variant(quote_deescaped, (mode,))

        duplicate_after_deescape = _repair_duplicate_property_quote(quote_deescaped)
        if duplicate_after_deescape != quote_deescaped:
            add_variant(
                duplicate_after_deescape,
                (mode, "duplicate_property_quote"),
            )

        comma_after_deescape = _remove_trailing_commas(quote_deescaped)
        if comma_after_deescape != quote_deescaped:
            add_variant(
                comma_after_deescape,
                (mode, "trailing_comma"),
            )

        quote_variant = quote_deescaped

    # Apply one additional syntax-only cleanup to variants created above.  This keeps the search
    # bounded while covering combinations such as JSON-string wrapping plus a trailing comma.
    initial_variants = list(variants)
    for value, normalizations in initial_variants[1:]:
        duplicate = _repair_duplicate_property_quote(value)
        if duplicate != value:
            add_variant(
                duplicate,
                (*normalizations, "duplicate_property_quote"),
            )

        comma_fixed = _remove_trailing_commas(value)
        if comma_fixed != value:
            add_variant(
                comma_fixed,
                (*normalizations, "trailing_comma"),
            )

    # Missing final delimiters are only completed after all non-structural cleanups.  Completion
    # refuses mismatched interior delimiters and unterminated strings.
    initial_variants = list(variants)
    for value, normalizations in initial_variants:
        completed = _complete_trailing_delimiters(value)
        if completed is not None and completed != value:
            add_variant(
                completed,
                (*normalizations, "trailing_delimiter_completion"),
            )

    repaired_variants = sorted(
        variants[1:],
        key=lambda item: (
            sum(_NORMALIZATION_COST[mode] for mode in item[1]),
            len(item[1]),
        ),
    )
    for value, normalizations in repaired_variants:
        candidate = _find_candidate(value, keys)
        if candidate is None:
            continue
        embedded = candidate.start != 0 or candidate.end != len(value)
        modes = normalizations
        if embedded:
            modes = (*modes, "embedded_json")
        return json_object_decode_result(
            value=candidate.value,
            normalizations=modes,
        )

    return None


def _normalize_required_keys(required_keys: Iterable[str]) -> frozenset[str]:
    keys: set[str] = set()
    for key in required_keys:
        if not isinstance(key, str) or not key:
            raise ValueError("Required JSON object keys must be non-empty strings.")
        keys.add(key)
    return frozenset(keys)


def _find_candidate(
    text: str,
    required_keys: frozenset[str],
) -> _candidate | None:
    decoder = json.JSONDecoder()
    best: _candidate | None = None

    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, relative_end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        if required_keys and not required_keys.issubset(value):
            continue

        candidate = _candidate(
            value=value,
            start=index,
            end=index + relative_end,
        )
        if best is None:
            best = candidate
            continue

        # Prefer the earliest candidate.  If two start at the same location, prefer the larger
        # object.  This prevents a later decodable child object from replacing the intended root.
        if candidate.start < best.start:
            best = candidate
        elif candidate.start == best.start and candidate.end > best.end:
            best = candidate

    return best


def _repair_duplicate_property_quote(text: str) -> str:
    return _DUPLICATE_PROPERTY_QUOTE.sub(r'\1"\2":', text)


def _remove_trailing_commas(text: str) -> str:
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0

    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue

        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "]}":
                index += 1
                continue

        output.append(char)
        index += 1

    return "".join(output)


def _complete_trailing_delimiters(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None

    prefix = text[:start]
    payload = text[start:]
    stack: list[str] = []
    in_string = False
    escaped = False
    closing = {
        "{": "}",
        "[": "]",
    }
    opening = {
        "}": "{",
        "]": "[",
    }

    for char in payload:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char in closing:
            stack.append(char)
            continue
        if char in opening:
            if not stack or stack[-1] != opening[char]:
                return None
            stack.pop()

    if in_string or not stack:
        return None

    suffix = "".join(closing[char] for char in reversed(stack))
    return prefix + payload + suffix
