from __future__ import annotations

from acmm.evidence.structured import decode_json_object_text


def test_decode_json_object_text_accepts_exact_object() -> None:
    result = decode_json_object_text(
        '{"result":{"value":6}}',
        required_keys=("result",),
    )

    assert result is not None
    assert result.value == {"result": {"value": 6}}
    assert result.normalizations == ()


def test_decode_json_object_text_accepts_fenced_or_trailing_object() -> None:
    result = decode_json_object_text(
        '```json\n{"result":{"value":6}}\n```',
        required_keys=("result",),
    )

    assert result is not None
    assert result.value == {"result": {"value": 6}}
    assert result.normalizations == ("embedded_json",)


def test_decode_json_object_text_unwraps_json_string() -> None:
    result = decode_json_object_text(
        '"{\\"result\\":{\\"value\\":6}}"',
        required_keys=("result",),
    )

    assert result is not None
    assert result.value == {"result": {"value": 6}}
    assert result.normalizations == ("json_string_unwrap",)


def test_decode_json_object_text_completes_missing_trailing_delimiters() -> None:
    result = decode_json_object_text(
        '{"result":{"items":[{"id":"A"},{"id":"B"}]}',
        required_keys=("result",),
    )

    assert result is not None
    assert result.value == {
        "result": {
            "items": [
                {"id": "A"},
                {"id": "B"},
            ]
        }
    }
    assert result.normalizations == ("trailing_delimiter_completion",)


def test_decode_json_object_text_deescapes_one_presentation_quote_layer() -> None:
    result = decode_json_object_text(
        '{"result":{"items":[{"id":"A"},{\\"id\\":\\"B\\"}]}}"}',
        required_keys=("items",),
    )

    assert result is not None
    assert result.value == {
        "items": [
            {"id": "A"},
            {"id": "B"},
        ]
    }
    assert result.normalizations == ("quote_deescape", "embedded_json")


def test_decode_json_object_text_repairs_fully_escaped_object_text() -> None:
    result = decode_json_object_text(
        r'{\"result\":{\"assignments\":{\"A\":\"X\"}}}',
        required_keys=("result",),
    )

    assert result is not None
    assert result.value == {"result": {"assignments": {"A": "X"}}}
    assert result.normalizations == ("quote_deescape",)


def test_decode_json_object_text_repairs_duplicate_property_opening_quote() -> None:
    result = decode_json_object_text(
        '{"result":{"assignments":{"A":"X",""I":"Z"}}}',
        required_keys=("result",),
    )

    assert result is not None
    assert result.value == {
        "result": {
            "assignments": {
                "A": "X",
                "I": "Z",
            }
        }
    }
    assert result.normalizations == ("duplicate_property_quote",)


def test_decode_json_object_text_repairs_trailing_comma() -> None:
    result = decode_json_object_text(
        '{"result":{"items":[1,2,],}}',
        required_keys=("result",),
    )

    assert result is not None
    assert result.value == {"result": {"items": [1, 2]}}
    assert result.normalizations == ("trailing_comma",)


def test_decode_json_object_text_does_not_return_arbitrary_child_object() -> None:
    result = decode_json_object_text(
        '{"outer":[{"id":"A"},{"id":"B"}]}',
        required_keys=("result",),
    )

    assert result is None


def test_decode_json_object_text_refuses_mismatched_interior_delimiters() -> None:
    result = decode_json_object_text(
        '{"result":{"items":[1,2}}',
        required_keys=("result",),
    )

    assert result is None


def test_decode_json_object_text_prefers_delimiter_completion_over_quote_deescape() -> None:
    result = decode_json_object_text(
        '{"result":{"text":"say \\"hi\\""}',
        required_keys=("result",),
    )

    assert result is not None
    assert result.value == {"result": {"text": 'say "hi"'}}
    assert result.normalizations == ("trailing_delimiter_completion",)

