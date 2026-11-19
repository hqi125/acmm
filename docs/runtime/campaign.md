# Durable Campaign execution

`acmm campaign` freezes one installed Experiment Package revision, its Contract, its
materialization design, and the complete logical run population into a durable artifact.
Campaign execution delegates immediate work to the same `execute_experiment()` service as
`acmm run`; Campaign adds durable selection history, reconciliation, resumption, and merging.

## Commands

Create a frozen Campaign:

```text
acmm campaign create <experiment-selector>
```

Execute any pending subset. Scenario selectors may be repeated and are reordered only by the
frozen materialization policy; logical run indices do not change:

```text
acmm campaign execute <campaign-id> \
  --scenario <scenario-a> \
  --scenario <scenario-b> \
  --repetition 1 \
  --apply-if-needed \
  --start-if-needed \
  --capture-runtime-logs
```

Inspect durable state without mutation:

```text
acmm campaign status <campaign-id>
```

Resume the pending logical slots from the most recent incomplete Campaign execution selection:

```text
acmm campaign resume <campaign-id> \
  --apply-if-needed \
  --start-if-needed \
  --capture-runtime-logs
```

A specific prior execution can be selected explicitly:

```text
acmm campaign resume <campaign-id> --execution <execution-id> ...
```

A resume always creates a new immutable execution session. The new Campaign Execution record
stores `resumed_from_execution_id`; the interrupted/failed source execution is never rewritten
as if it had continued in place.

## Run state

New Campaign Execution records use schema v0.2. A logical Campaign run is either:

- **observed**: a scientifically valid accepted attempt is durably persisted; or
- **pending**: no accepted observation exists yet and the logical run remains resumable once the
  Campaign recovery boundary is safe.

The old v0.1 per-run `blocked` state is retired. v0.1 Campaign Execution artifacts remain
readable; their `blocked_run_ids` are interpreted as pending/resumable. Exposure to an agent no
longer permanently consumes a logical slot because the current runtime can retry from a fresh
agent session and fresh materialization generation.

Safety is enforced at the Campaign execution/recovery boundary instead. Before new work begins,
Campaign reconciles unfinished execution metadata. Failed or unclean executions that used a
runtime lifecycle must establish a safe external-state boundary through the frozen package's
recovery hook before pending logical runs may resume. Typed interruptions also use that recovery
hook when one is available; packages without a recovery hook may resume a typed interruption
because the immediate runtime already crossed its controlled interruption-cleanup boundary.

## Retry versus resume

`--max-attempts-per-run` controls fresh attempts inside one immediate execution and defaults to
2, matching `acmm run`. Invalid attempts are retried by the runtime using a fresh subject session
and fresh materialization according to the normal runtime contract.

Campaign resume is a different layer. It starts a new Campaign execution for logical slots that
remain pending after a prior execution completed partially, was interrupted, or was safely
recovered. It does not resume an agent conversation or mutate the old execution session.

## Multi-scenario materialization

Campaign freezes the package-declared `campaign.materialization.group_by` policy. A package such
as Experiment 2 that groups by `scenario_id` receives one materialization slice per selected
scenario. Within each slice Core keeps subject-major ordering and preserves run-index order within
a subject. Selecting multiple scenarios therefore exercises multiple materialization slices in one
Campaign execution without changing the logical experiment design.

## Merge

`acmm campaign merge` copies accepted observations into an immutable merged product. Complete
merge requires every logical run to be observed. `--allow-partial` permits an explicitly partial
merge and records total, observed, and pending population counts. Campaign Merge v0.2 retires the
old `blocked` population field; v0.1 merge artifacts remain valid historical records.
