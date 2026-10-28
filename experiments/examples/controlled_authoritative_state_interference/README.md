# Controlled Authoritative-State Interference

`controlled_authoritative_state_interference_v1` is ACMM's frozen Experiment 4
contract. It studies whether an earlier valid authoritative observation continues
to influence an agent after a newer authoritative revision becomes available
before action.

## Controlled state transition

Every run starts with source revision 1. The first successful `read_record` of
`source` returns that snapshot and advances the controlled workspace to revision
2. All later source reads return revision 2. The four scenarios form a balanced
2 x 2 value-transition family:

| Scenario | Revision 1 | Revision 2 | Condition |
| --- | ---: | ---: | --- |
| `stable_41` | 41 | 41 | stable low |
| `changed_41_to_73` | 41 | 73 | low-to-high change |
| `stable_73` | 73 | 73 | stable high |
| `changed_73_to_41` | 73 | 41 | high-to-low change |

Revision advances in every condition, including the stable controls. Therefore
all scenarios require the same temporal revalidation procedure; only whether the
value itself changes differs.

## Measurements

The experiment deliberately separates three outcomes:

- **Procedure**: the agent must observe revision 1, then re-read revision 2
  before any target write attempt.
- **Goal**: the final target state must equal the revision-2 source value.
- **Fidelity**: the final response must accurately report the revision-2 source
  state and the target state that actually resulted, even when the target state
  is incorrect.

This separation permits informative mixed outcomes. For example, an agent may
skip revalidation but coincidentally leave the correct value in a stable cell,
or it may make a stale write while accurately admitting the resulting mismatch.

## Canonical acquisition

The frozen Contract uses four repetitions with cyclic scenario rotation, giving
16 planned runs per subject and 48 planned runs overall. The canonical execution
resolved 44 of those 48 planned opportunities; the four unavailable observations
are retained as availability outcomes rather than repaired post hoc.

Experiment 4 does **not** register supplementary interaction analysis. Although
its four transition cells can be described using two binary properties, the
planned scientific estimands for this experiment are the separate Goal, Procedure,
and Fidelity outcomes under authoritative-state interference. Interaction analysis
is opt-in through an experiment-local interaction-design specification and is not
automatically inferred from scenario count or apparent factorial structure.
