# Runtime Planning

## Purpose

Runtime planning is the first step after an Experiment Contract has passed Contract
validation.

The planner expands the declarative experiment definition into the concrete runs that the
runtime will eventually execute.

For Contract v0.1, the run population is:

```text
subjects x scenarios x repetitions
```

No agent interaction occurs during planning.

## Planned run

A planned run currently contains:

- `run_index`: zero-based position in the generated plan;
- `subject_id`: Contract subject selected for the run;
- `scenario_id`: Contract scenario selected for the run;
- `repetition`: one-based repetition number.

The planner does not yet assign a persistent run UUID or execution-result identity.
Durable run identity and provenance belong to the future Run Record design.

## Deterministic ordering

Plans preserve Contract declaration order within the ordering selected by
`run_protocol.ordering`. If the field is omitted, the historical Contract v0.1 order is
used:

```text
subject_scenario_repetition
```

Its expansion is:

```text
for each subject, in Contract order:
    for each scenario, in Contract order:
        for repetition 1 through N:
            emit one planned run
```

Interaction experiments may instead select:

```text
subject_repetition_scenario
```

which expands as:

```text
for each subject, in Contract order:
    for repetition 1 through N:
        for each scenario, in Contract order:
            emit one planned run
```

This keeps subject execution grouped while interleaving scenario conditions within each
repetition, reducing unnecessary confounding between scenario and execution time. Both
orders are deterministic.

### Scenario position balancing

An interleaved plan may additionally select:

```text
scenario_ordering: cyclic_rotation
```

The default is `declaration`, which preserves Contract scenario order in every repetition.
`cyclic_rotation` advances the declaration order by one position per repetition and phases
each subsequent subject by one starting position. For four scenarios `A`, `B`, `C`, `D`,
the first subject expands as:

```text
repetition 1: A B C D
repetition 2: B C D A
repetition 3: C D A B
repetition 4: D A B C
```

The second subject begins one phase later:

```text
repetition 1: B C D A
repetition 2: C D A B
repetition 3: D A B C
repetition 4: A B C D
```

This is deterministic **scenario-position counterbalancing**. Across every complete cycle of
`scenario_count` repetitions, each scenario occupies every within-repetition position once.
When the repetition count is not a multiple of the scenario count, position counts differ by
at most one. Phasing subjects also avoids assigning the same scenario to the first
post-acquisition position for every subject.

This strategy does **not** fully counterbalance carryover or transition effects: cyclic
rotations preserve a restricted set of adjacent scenario transitions. Experiments that need
transition-balanced designs should introduce a separate ordering strategy rather than
interpreting `cyclic_rotation` as a Williams/complete counterbalancing design.

`cyclic_rotation` is valid only with `ordering: subject_repetition_scenario`, because scenario
order must be selected independently for each repetition.

For two subjects `subject_a`, `subject_b`, two scenarios `scenario_a`, `scenario_b`, and
two repetitions, the result is:

```text
0  subject_a  scenario_a  1
1  subject_a  scenario_a  2
2  subject_a  scenario_b  1
3  subject_a  scenario_b  2
4  subject_b  scenario_a  1
5  subject_b  scenario_a  2
6  subject_b  scenario_b  1
7  subject_b  scenario_b  2
```

Given the same validated Contract, planning must produce the same ordered plan.

## Validation boundary

`build_experiment_plan()` assumes its input Contract has already passed the required
Contract validation layers.

The planner intentionally does not duplicate schema validation, ACMM semantic validation,
or harness-compatibility validation.

The intended flow is:

```text
Experiment Contract
        |
        v
Contract validation
        |
        v
Harness compatibility validation
        |
        v
Runtime planning
        |
        v
Planned runs
        |
        v
Future execution layer
```

This keeps planning focused on deterministic expansion rather than policy enforcement.

## CLI inspection

The run plan can be inspected without execution:

```text
acmm plan experiments/examples/magic_number/experiment.yaml
```

The command performs Contract validation first. Errors prevent plan generation; warnings
are reported but do not block planning.

The output includes the experiment ID, total run count, and one row per planned run.
