# Controlled Authoritative-State Interference

`controlled_authoritative_state_interference_v1` is the initial Experiment 4
qualification contract. It studies whether an earlier valid authoritative
observation continues to influence an agent after a newer authoritative
revision becomes available before action.

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

## Qualification status

The current Contract uses four repetitions with cyclic scenario rotation. This
is an engineering/qualification setting chosen so each scenario occupies every
within-repetition position exactly once for each subject. It is **not yet the
frozen Experiment 4 sample size**. The canonical repetition count should be
selected only after the stateful tool apparatus is implemented and live pilots
confirm the intended behavior across all three subjects.
