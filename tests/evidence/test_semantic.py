from acmm.evidence.semantic import (
    output_scalar_semantics,
    output_scalar_semantics_for_path,
    semantic_scalar_equal,
)


def test_semantic_integer_accepts_native_and_declared_canonical_string() -> None:
    policy = output_scalar_semantics(
        path="result.configuration.quota.value",
        semantic_type="integer",
        accepted_serializations=("native", "canonical_string"),
    )

    assert semantic_scalar_equal(6, 6, policy)
    assert semantic_scalar_equal("6", 6, policy)
    assert not semantic_scalar_equal("06", 6, policy)
    assert not semantic_scalar_equal("6.0", 6, policy)
    assert not semantic_scalar_equal(True, 1, policy)


def test_semantic_boolean_string_is_only_accepted_when_explicitly_declared() -> None:
    strict = output_scalar_semantics(
        path="result.enabled",
        semantic_type="boolean",
        accepted_serializations=("native",),
    )
    relaxed = output_scalar_semantics(
        path="result.enabled",
        semantic_type="boolean",
        accepted_serializations=("native", "canonical_string"),
    )

    assert semantic_scalar_equal(False, False, strict)
    assert not semantic_scalar_equal("false", False, strict)
    assert semantic_scalar_equal("false", False, relaxed)
    assert not semantic_scalar_equal("False", False, relaxed)


def test_semantic_number_accepts_json_number_strings_without_boolean_coercion() -> None:
    policy = output_scalar_semantics(
        path="result.score",
        semantic_type="number",
        accepted_serializations=("native", "canonical_string"),
    )

    assert semantic_scalar_equal(6.0, 6, policy)
    assert semantic_scalar_equal("6", 6.0, policy)
    assert semantic_scalar_equal("6.0", 6, policy)
    assert semantic_scalar_equal("6e0", 6, policy)
    assert not semantic_scalar_equal("+6", 6, policy)
    assert not semantic_scalar_equal(True, 1, policy)


def test_semantic_string_remains_exact() -> None:
    policy = output_scalar_semantics(
        path="result.mode",
        semantic_type="string",
        accepted_serializations=("native",),
    )

    assert semantic_scalar_equal("ENV", "ENV", policy)
    assert not semantic_scalar_equal("env", "ENV", policy)
    assert not semantic_scalar_equal(6, "6", policy)


def test_output_scalar_semantics_lookup_uses_exact_declared_path() -> None:
    scenario = {
        "task": {
            "prompt": "example",
            "output_semantics": {
                "scalar_fields": [
                    {
                        "path": "result.configuration.quota.value",
                        "semantic_type": "integer",
                        "accepted_serializations": ["native", "canonical_string"],
                    }
                ]
            },
        }
    }

    policy = output_scalar_semantics_for_path(
        scenario,
        "result.configuration.quota.value",
    )
    assert policy is not None
    assert policy.semantic_type == "integer"
    assert policy.accepted_serializations == ("native", "canonical_string")
    assert output_scalar_semantics_for_path(scenario, "result.configuration.mode.value") is None
