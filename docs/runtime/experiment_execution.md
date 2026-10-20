# Experiment-Level Execution

## Purpose

Experiment-level execution turns selected slots from the deterministic Contract plan into one
or more persisted realized attempts. The default selection is the complete plan; explicit
run-index selection is reserved for engineering/pilot executions.

This layer performs data acquisition only. It does not compute ACMM compliance proportions,
dimension scores, confidence intervals, statistical models, or plots.

The execution path is:

```text
validated Experiment Contract
        |
        v
experiment_plan
        |
        v
execute_experiment()
        |
        +-- planned slot 0
        |      +-- attempt 1
        |      +-- optional infrastructure retry
        |
        +-- planned slot 1
        |      +-- attempt 1
        |
        +-- ...
        |
        v
completed execution session
```

## Execution session

One call to `execute_experiment()` creates one execution session:

```text
results/
└── <experiment_id>/
    └── execution_<execution_id>/
        ├── contract.yaml
        ├── experiment_execution.yaml
        └── runs/
            ├── run_<run_index>_<run_id>/
            │   ├── run_record.yaml
            │   └── evidence/
            └── ...
```

`contract.yaml` is an exact byte-for-byte snapshot of the Experiment Contract artifact used
for the session. Every Run Record stores the SHA-256 digest of those exact bytes.

The execution UUID identifies this orchestration invocation. It is separate from each
realized run UUID.

For a full execution, every Contract run index is selected. For an explicit subset execution,
the manifest records:

```text
contract_run_count
selected_run_indices
planned_run_count
```

so a one-slot smoke test cannot be confused with a complete Contract acquisition.

## Completion marker

`experiment_execution.yaml` is written only after the selected plan has been traversed.

Therefore:

```text
execution_<id>/ with experiment_execution.yaml
    = completed orchestration session

execution_<id>/ without experiment_execution.yaml
    = incomplete/interrupted session
```

Already persisted per-run directories inside an incomplete session remain individually
useful Run Records. The absence of the completion manifest prevents the partial session from
silently appearing complete.

The completion manifest itself is written through a temporary sibling file followed by an
atomic replacement.

## Planned slots and attempts

A planned slot remains identified by:

```text
run_index
subject_id
scenario_id
repetition
```

Every realized attempt receives its own `run_id`.

The session manifest records attempt order explicitly:

```text
planned slot
    |
    +-- attempt 1 -> invalid execution
    |
    +-- attempt 2 -> valid execution <- selected_run_id
```

Invalid attempts are never overwritten or hidden.

## Inter-run pacing

A Contract may declare:

```yaml
run_protocol:
  pacing:
    inter_run_delay_seconds: 2
```

The executor waits that fixed duration only between selected planned slots. The sequence is:

```text
slot 0
    +-- attempt 1
    +-- optional invalid retries
    +-- slot complete
    |
    +-- fixed inter-run delay
    |
slot 1
```

No pacing delay occurs between retries of the same planned slot, because retry policy is a
separate acquisition concern. No delay occurs after the final selected slot. A one-slot
engineering execution therefore never sleeps merely because the Contract contains pacing.

The completion manifest records the configured pacing policy under:

```text
execution.pacing.inter_run_delay_seconds
```

The delay is deterministic rather than randomized. This keeps provider-facing request
bursts conservative while preserving straightforward experimental reproducibility.

## Retry semantics

`max_attempts_per_run` is an operational acquisition policy. It applies only when the
previous realized execution has:

```text
execution.validity = invalid
```

An invalid execution indicates infrastructure/orchestration failure and does not constitute
agent compliance evidence.

The first attempt with:

```text
execution.validity = valid
```

resolves the planned slot and stops retrying.

This is true regardless of the observed agent termination. For example:

```text
termination = timeout
validity    = valid
```

is real experimental behavior, so it is selected and not retried.

If every allowed attempt is invalid, the planned slot remains unresolved. The completion
manifest records its run index in `unresolved_run_indices`.

## Selected attempt

For each resolved slot, `selected_run_id` identifies the first scientifically valid
execution.

This is acquisition metadata, not a statistical calculation.

All invalid attempts remain in the canonical result collection for auditability and
infrastructure diagnostics. Downstream data export can use `selected_run_id` to produce an
analysis-ready observation set without deleting the failed acquisition attempts.

## Failure behavior

A failure to persist a realized run aborts the experiment-level runner. Previously persisted
runs remain intact, the exact Contract snapshot remains available, and no completion
manifest is written.

This deliberately favors preservation over rollback: once a realized run has been safely
published, a later infrastructure failure does not delete it.

## Researcher-facing command

The first registered live runtime is the Hermes magic-number pilot. With the managed Hermes
deployment already `READY`, execute one deterministic slot with:

```text
acmm run experiments/examples/magic_number/experiment.yaml --run-index 0
```

The command performs Contract validation, harness-compatibility validation, execution,
persistence, and a second Run Record validation against the exact Contract SHA-256. A valid
scientific execution resolves the selected slot even when its compliance judgments contain
violations. Infrastructure-invalid attempts leave the slot unresolved and produce a nonzero
command status.

Omitting `--run-index` executes the entire Contract plan.

## Current exclusions

Experiment-level execution does not yet:

- provide Open WebUI or Agent Zero execution bindings;
- provide a general runtime-binding registry beyond the current Hermes magic-number pilot;
- validate a complete result collection across multiple sessions;
- export CSV analysis tables;
- compute ACMM estimators or scores;
- invoke R or RStudio.

Those are separate subsequent layers.
