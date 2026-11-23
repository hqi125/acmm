# Durable Campaign Execution

`acmm campaign` freezes one installed Experiment Package revision, its Contract, materialization design, and complete logical run population into a durable study artifact. Campaign execution reuses the same immediate execution machinery as `acmm run`, but adds durable selection history, resumption, Campaign-level adjudication, and immutable merge products.

## Create a Campaign

Preview the frozen design without writing a Campaign:

```text
acmm campaign create <experiment-selector> --preview
```

Create it:

```text
acmm campaign create <experiment-selector>
```

The command returns a Campaign ID. The generated artifact is stored beneath `<generated-root>`; set `ACMM_GENERATED_ROOT` before the command when the complete generated tree should be redirected.

## Execute selected pending runs

Preview a selection:

```text
acmm campaign execute <campaign-id> \
  --scenario <scenario-id> \
  --repetition 1 \
  --preview
```

Execute it:

```text
acmm campaign execute <campaign-id> \
  --scenario <scenario-id> \
  --repetition 1 \
  --apply-if-needed \
  --start-if-needed \
  --capture-runtime-logs
```

Selectors operate against the frozen Campaign population. Logical run indices do not change across Campaign executions.

Inspect durable state without mutation:

```text
acmm campaign status <campaign-id>
```

## Resume an incomplete execution selection

Resume pending logical slots from the most recent resumable Campaign execution:

```text
acmm campaign resume <campaign-id> \
  --apply-if-needed \
  --start-if-needed \
  --capture-runtime-logs
```

A specific prior Campaign execution can be selected explicitly:

```text
acmm campaign resume <campaign-id> --execution <execution-id> --apply-if-needed --start-if-needed
```

Resume always creates a new immutable Campaign execution session. It never reopens an agent conversation or rewrites the interrupted/failed source execution as though it had continued in place.

## Run state and retry boundary

A logical Campaign run is either:

- **observed** — a scientifically valid accepted attempt has been durably persisted; or
- **pending** — no accepted observation exists yet and the slot remains resumable once the recovery boundary is safe.

`--max-attempts-per-run` controls fresh attempts inside one immediate execution. Campaign resume is a separate layer: it starts a new Campaign execution for logical slots still pending after a prior execution stopped, failed safely, or completed only part of its selection.

When a package owns external mutable state, Campaign recovery requires the package/runtime lifecycle to establish a safe boundary before the logical slot can be executed again.

## Materialization

A Campaign freezes the package-declared materialization policy. Selecting a subset does not redefine the scientific design: it schedules only the matching frozen logical slots and preserves their original identities.

## Campaign adjudication

Campaign adjudication is intentionally a **whole-Campaign** operation. It re-evaluates the accepted evidence of a complete Campaign with adjudication code from an explicit installed package revision whose Contract exactly matches the frozen Campaign Contract.

```text
acmm campaign adjudicate <campaign-id> --package <adjudicator-selector>
```

A partial Campaign is rejected rather than treated as an arbitrary per-run adjudication request. Adjudication creates a separate immutable artifact; it does not mutate acquisition Run Records.

Current writes use bounded Campaign Adjudication artifacts with a small provenance manifest plus a hash-bound `runs.jsonl` collection. Older supported adjudication formats remain readable.

## Merge

Create an immutable merge from accepted observations:

```text
acmm campaign merge <campaign-id>
```

Apply one complete Campaign adjudication when assembling the merged Run Records:

```text
acmm campaign merge <campaign-id> --adjudication <adjudication-id>
```

Complete merge requires the full logical population. For diagnostics or smoke tests, an explicitly partial merge is available:

```text
acmm campaign merge <campaign-id> --allow-partial
```

A partial merge records total, observed, and pending population counts. It is visibly distinct from a complete study merge.

Current writes use Campaign Merge v0.4: the top-level manifest remains small and references a hash-bound `observations.jsonl` collection. Older supported merge formats remain readable historical artifacts.

## Progress output

Campaign execution, adjudication, and merge emit coarse phase-level progress. Long operations report durable progress without printing one line per source file or logical run.

## Related documentation

- [`experiment_execution.md`](experiment_execution.md) — immediate execution semantics and retries.
- [`materialization.md`](materialization.md) — package materialization lifecycle.
- [`../analysis/export.md`](../analysis/export.md) — Campaign export requires both the Campaign and one immutable merge.
