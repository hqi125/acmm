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

Plans preserve Contract declaration order.

The expansion order is:

```text
for each subject, in Contract order:
    for each scenario, in Contract order:
        for repetition 1 through N:
            emit one planned run
```

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
