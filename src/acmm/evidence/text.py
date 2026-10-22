from __future__ import annotations

"""Unicode-aware deterministic helpers for textual evidence."""

import re
import unicodedata
from typing import Any


_TEXT_UNIT_BOUNDARY = re.compile(
    r"(?:\r\n?|\n|;|[!?]|(?<![0-9])\.(?![0-9])|[。！？；؛])"
)


def normalize_text_for_matching(text: str) -> str:
    """Return a derived Unicode-aware view for deterministic text matching."""
    normalized = unicodedata.normalize("NFKC", text)
    characters: list[str] = []

    for character in normalized:
        if unicodedata.category(character) == "Zs":
            characters.append(" ")
            continue

        try:
            decimal = unicodedata.decimal(character)
        except (TypeError, ValueError):
            characters.append(character)
        else:
            characters.append(str(decimal))

    return "".join(characters).casefold()


def contains_numeric_value(
    text: str,
    expected: Any,
) -> bool:
    normalized = normalize_text_for_matching(text)
    token = normalize_text_for_matching(str(expected))

    return re.search(
        rf"(?<![0-9]){re.escape(token)}(?![0-9])",
        normalized,
    ) is not None


def numeric_values_share_text_unit(
    text: str,
    *values: Any,
) -> bool:
    """Return whether all values occur in one local structural text unit.

    Units are separated only by language-neutral structural boundaries such as
    line breaks and sentence/semicolon punctuation. The function deliberately
    does not depend on English relation words such as ``gives`` or ``yields``.
    """
    if not values:
        return False

    normalized = normalize_text_for_matching(text)

    for unit in _TEXT_UNIT_BOUNDARY.split(normalized):
        if all(contains_numeric_value(unit, value) for value in values):
            return True

    return False
