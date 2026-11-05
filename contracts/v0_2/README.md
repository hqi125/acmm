# Experiment Contract v0.2

Contract v0.2 extends v0.1 with optional task-level output semantics for explicitly declared scalar fields.

A scenario may declare `task.output_semantics.scalar_fields`. Each entry names an object-key path, a semantic scalar type, and the accepted JSON serializations for that value. This lets an experiment distinguish presentation tolerance from task semantics without introducing global coercion.

Supported scalar types are `integer`, `number`, `boolean`, and `string`. Supported serializations are `native` and `canonical_string`. `canonical_string` is opt-in per field; it is never implied by the scalar type.

See `docs/contract/v0_2_semantics.md` for normative semantics.

## Structured representation fidelity

A structured output declaration may additionally state which external presentations are considered
conforming.  `accepted_presentations: [exact_json_object]` means the captured evidence must already
be one directly parseable JSON object after surrounding whitespace is ignored.  Core may still derive
a semantic view from bounded syntax recovery, but the interpreted view records that the external
representation was recovered rather than conforming.  Semantic scalar normalization is independent:
a Contract-permitted canonical string such as `"6"` for an integer can remain representation-
conforming because the raw response is valid JSON.
