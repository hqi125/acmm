from __future__ import annotations

from acmm.evidence.text import (
    contains_numeric_value,
    normalize_text_for_matching,
    numeric_values_share_text_unit,
)


def test_normalize_text_for_matching_handles_compatibility_spacing_and_digits() -> None:
    text = "ADDEND ١٣ → RESULT ١٠٠"

    assert normalize_text_for_matching(text) == "addend 13 → result 100"


def test_contains_numeric_value_matches_unicode_decimal_digits() -> None:
    assert contains_numeric_value("النتيجة ١٠٠", 100)
    assert not contains_numeric_value("النتيجة ١٠٠٠", 100)


def test_numeric_values_share_text_unit_is_relation_word_independent() -> None:
    responses = (
        "Adding 13 to 87 yields 100. Adding 29 to 87 yields 116.",
        "13 + 87 = 100; 29 + 87 = 116.",
        "13 与 87 相加，结果为 100。29 与 87 相加，结果为 116。",
        "١٣ + ٨٧ = ١٠٠؛ ٢٩ + ٨٧ = ١١٦",
    )

    for response in responses:
        assert numeric_values_share_text_unit(response, 13, 100)
        assert numeric_values_share_text_unit(response, 29, 116)


def test_numeric_values_share_text_unit_respects_structural_separation() -> None:
    responses = (
        "13 + 87 = 116; 29 + 87 = 100.",
        "١٣ + ٨٧ = ١١٦؛ ٢٩ + ٨٧ = ١٠٠",
    )

    for response in responses:
        assert not numeric_values_share_text_unit(response, 13, 100)
        assert not numeric_values_share_text_unit(response, 29, 116)
