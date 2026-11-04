# Experiment Contract v0.2

Contract v0.2 extends v0.1 with optional task-level output semantics for explicitly declared scalar fields.

A scenario may declare `task.output_semantics.scalar_fields`. Each entry names an object-key path, a semantic scalar type, and the accepted JSON serializations for that value. This lets an experiment distinguish presentation tolerance from task semantics without introducing global coercion.

Supported scalar types are `integer`, `number`, `boolean`, and `string`. Supported serializations are `native` and `canonical_string`. `canonical_string` is opt-in per field; it is never implied by the scalar type.

See `docs/contract/v0_2_semantics.md` for normative semantics.
