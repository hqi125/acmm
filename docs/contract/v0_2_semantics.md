# ACMM Experiment Contract v0.2 Semantics

Contract v0.2 preserves the v0.1 experiment structure and adds optional declarations that bind raw evidence into an interpreted structured-output view and describe explicit scalar representation semantics:

```yaml
task:
  prompt: Return a JSON result.
  output_semantics:
    structured_object:
      evidence_id: final_response
      payload_path: result
      required_payload_keys:
        - configuration
    scalar_fields:
      - path: result.configuration.quota.value
        semantic_type: integer
        accepted_serializations:
          - native
          - canonical_string
```

The declaration exists to separate **representation tolerance** from **task correctness**. It does not cause Core to guess missing values, reinterpret arbitrary strings, or weaken evaluator-specific task semantics.

## Structured output binding

`structured_object` tells Core which immutable evidence channel carries the structured result and how to bind it into the evaluator-facing payload. `evidence_id` must be captured by `evidence_policy`. `payload_path` is an exact dot-separated object path, and `required_payload_keys` prevents a malformed outer response from degrading into an unrelated nested object.

Core derives the structured view once before adjudication. Raw evidence bytes are never rewritten. Package evaluators receive the derived view through `evaluation_context.interpreted_evidence` and should judge `view.payload` rather than reparsing `final_response` themselves.

Syntax normalization provenance is attached automatically to judgment detail when a judgment cites the interpreted evidence.

## Scalar field paths

`path` is an exact dot-separated object-key path. v0.2 supports identifier-like path segments only. Array indexing, wildcards, and general JSONPath expressions are intentionally out of scope.

Two entries in one scenario may not declare the same path.

## Semantic types

v0.2 defines four scalar semantic types:

- `integer` — a JSON integer, excluding booleans;
- `number` — a JSON integer or floating-point number, excluding booleans;
- `boolean` — a JSON boolean;
- `string` — a JSON string.

The type describes what the field *means*. It does not by itself enable any alternate serialization.

## Accepted serializations

Each scalar declaration explicitly lists its accepted serializations.

### `native`

The returned JSON value must use the native JSON representation for the declared semantic type.

Examples:

- integer `6`;
- number `6.0`;
- boolean `false`;
- string `"ENV"`.

### `canonical_string`

The returned value may instead be a JSON string containing the canonical textual form of the declared scalar value. This is opt-in per field.

For `integer`, accepted strings use canonical base-10 integer syntax. `"6"` may represent integer `6`; `"06"`, `"+6"`, and `"6.0"` do not.

For `number`, accepted strings use JSON-number syntax. Numerically equivalent forms such as `"6"`, `"6.0"`, and `"6e0"` may represent number `6`.

For `boolean`, only lowercase `"true"` and `"false"` are canonical. Values such as `"False"`, `"0"`, `"off"`, and `"no"` are never inferred as booleans.

`canonical_string` is invalid for semantic type `string`, because JSON string is already its native representation.

## Strictness rule

No serialization is accepted unless the scenario declares it. A field declared as:

```yaml
semantic_type: boolean
accepted_serializations:
  - native
```

accepts JSON `false` and rejects JSON `"false"`.

This explicitness is deliberate. In large experiments, an over-permissive evaluator can silently convert genuine model misunderstandings into false-positive compliance. ACMM therefore treats representation relaxation as visible experiment intent rather than a global convenience setting.

## Runtime interpretation pipeline

The preferred adjudication path is:

1. adapters persist immutable raw evidence;
2. Core applies bounded syntax/presentation interpretation declared by `structured_object`;
3. Core applies only the scalar representation semantics explicitly declared by the Contract;
4. package evaluators receive the interpreted payload and determine scientific correctness; and
5. analysis consumes the resulting judgments.

The low-level helpers `decode_json_object_text()`, `output_scalar_semantics_for_path()`, and `semantic_scalar_equal()` remain public for specialized package logic, but ordinary structured-output evaluators should use `evaluation_context.interpreted_evidence`.

A syntax repair never implies semantic equivalence, and a semantic serialization policy never repairs malformed JSON.
