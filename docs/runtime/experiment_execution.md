# Experiment-Level Execution

## Purpose

Experiment-level execution turns selected slots from the deterministic Contract plan into one
or more persisted realized attempts. The default selection is the complete plan; explicit
run-index/range and subject selection is available for engineering/scoped executions.

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

The default generated-artifact root is `~/acmm_generated` (or `ACMM_GENERATED_ROOT` when configured). Immediate experiment sessions are stored beneath its `results/` namespace.

One call to `execute_experiment()` creates one execution session:

```text
~/acmm_generated/results/
└── <experiment_id>/
    └── execution_<execution_id>/
        ├── contract.yaml
        ├── experiment_execution.yaml
        ├── execution_resource_usage.yaml   # when the package provides a resource governor
        ├── execution_summary.txt
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

execution_<id>/ with experiment_interruption.yaml
    = explicitly interrupted session

execution_<id>/ without either manifest
    = incomplete session caused by an unhandled orchestration/persistence failure
```

Already persisted per-run directories inside an interrupted or incomplete session remain
individually useful Run Records. The absence of the completion manifest prevents the partial
session from silently appearing complete. Ctrl+C writes `experiment_interruption.yaml` with
the selected, resolved, unfinished, and not-started run indices plus the interrupted run/attempt
when one was active. Resume/continuation is intentionally unsupported so a later acquisition is
always a new execution session.

The completion manifest itself is written through a temporary sibling file followed by an
atomic replacement.

The YAML manifest structure is defined by the versioned JSON Schema shipped with ACMM Core:

```text
src/acmm/runtime/schemas/experiment-execution-v0.1.schema.json
```

`experiment_execution.yaml` remains the persisted researcher-facing artifact. JSON Schema is the
structural definition of that parsed YAML object; it is not a new researcher-facing JSON format and
does not add a new execution validity gate. The runtime schema version is centralized in
`acmm.runtime.execution_manifest`.

`execution_summary.txt` is a researcher-facing CLI report, not a scientific completion marker.
After persisted Run Records are re-validated, `acmm run` writes the detailed per-run summary to
this file instead of replaying every judgment and final response to the terminal. The report can
be regenerated from the canonical manifest, Run Records, and evidence artifacts if necessary.
Failure to write this convenience report does not invalidate an otherwise completed execution.

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


## External resource usage admission

An Experiment Package may provide a resource governor for provider-aware execution admission and usage measurement. The hook lives below execution entry points in `execute_experiment()`: immediate `acmm run` executions and Campaign executions therefore receive the same behavior without duplicating CLI policy.

Before each materialization slice, Core supplies the package with the materialization identity plus prior canonical resource-usage records discovered from sibling execution sessions. The package may wait before admitting the slice. After materialization finalization, the package returns the measured resource delta. Core persists both records in `execution_resource_usage.yaml`.

The schema intentionally leaves provider-specific admission and usage payloads open. Core guarantees only the durable envelope: execution identity/status, history-artifact references, materialization key/run indices, admission record, and observed usage record. A GitHub package may reason in rate-limit headers and content requests; another package can expose completely different resource dimensions without adding provider semantics to Core.

Cold start does not require a researcher-supplied baseline. If no canonical history exists, the package receives an empty history and is expected to use live provider state and its safe request-level behavior. Once one materialization completes, its measured usage is available to later slices in the same execution and later executions.

## Provider usage observability

When selected subjects use an Ollama Cloud LLM baseline, `acmm run` creates a best-effort Ollama
Cloud usage monitor. Core resolves the credential only from the Core-scoped ACMM secret binding
`core:ollama_api_key`. When the binding is available, ACMM samples the official cloud usage API
immediately before and after experiment-level execution and stores the normalized snapshots plus
account/model deltas under `execution.provider_usage.ollama_cloud`. The key and its local source path
are not persisted or mounted into subject runtimes. The usage API calls occur outside the execution
start/end timestamps so telemetry latency does not inflate recorded execution duration.

If the API key is absent, the API is unreachable, authentication fails, or the response cannot be
interpreted, experiment acquisition continues normally. Telemetry is diagnostic provenance only; it
never affects Run Record validity, ACMM judgments, dimension scores, availability analysis, or retry
policy. The final console report and `execution_summary.txt` include an Ollama Cloud request-count
line. ACMM prefers complete selected-model deltas, falls back to the account delta when model groups
are incomplete, and prints `N/A` with the reason when telemetry is unavailable.

Local Ollama cloud aliases such as `gpt-oss:120b-cloud` may be reported by the direct cloud usage API
as `gpt-oss:120b`; ACMM accepts the canonical non-`-cloud` provider name for telemetry matching while
preserving the configured model identity elsewhere. A zero delta means the sampled provider counters
did not advance; it is not proof that the agent made no inference request. See
`docs/runtime/provider_usage.md` for operational setup and interpretation guidance.

The usage endpoint is account-scoped rather than execution-scoped. Concurrent Ollama activity may
therefore contaminate the before/after delta. Per-model groups may also be absent when the provider
omits a model from its returned group limit, and a rolling-window boundary can change account
totals during a long execution. ACMM preserves the before/after observations and marks incomplete
per-model deltas rather than treating them as exact experimental measurements.

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

Provider retry exhaustion is represented as `termination = provider_error` with
`validity = invalid`, so it follows the same retry/unresolved-slot rule rather than being
misclassified as agent behavior.

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

For a single-slot Hermes smoke test, with the managed deployment already `READY`, execute one
deterministic slot with:

```text
acmm run <experiment-selector> --run-index 0
```

The command performs Contract validation, harness-compatibility validation, execution,
persistence, and a second Run Record validation against the exact Contract SHA-256. A valid
scientific execution resolves the selected slot even when its compliance judgments contain
violations. Infrastructure-invalid attempts leave the slot unresolved and produce a nonzero
command status.

During acquisition, per-run progress remains visible. At completion, the terminal prints only a
compact execution summary: session and manifest paths, the detailed summary-file path, selected
and resolved counts, realized attempt count, selected-run termination counts, UTC start/end
timestamps, and total wall-clock duration. Detailed per-run judgments, tool-call names, failures,
record paths, and final responses are written to `execution_summary.txt`.

Run selection supports global plan indices, subject-relative indices, Contract subject IDs, scenario
IDs, and repetition numbers:

```text
acmm run <experiment-selector> \
    --run-index 20-39 --subject <subject-id>

acmm run <experiment-selector> \
    --subject <subject-id> --subject-run-index 0-9

acmm run <experiment-selector> \
    --subject <subject-id> --scenario <scenario-id> --repetition 1
```

Each `--run-index` occurrence accepts a global `INDEX` or `START-END`; repeated selectors are unioned
and overlap is deduplicated. Repeated `--subject` selectors are unioned, and global run-index and
subject filters are intersected. `--subject-run-index` uses the same zero-based syntax but indexes
within each selected subject's deterministic run subsequence. It requires at least one `--subject`
and is mutually exclusive with `--run-index`. With multiple subjects, the same relative selectors
are applied independently to each selected subject. Relative positions are defined after the
Contract's deterministic ordering and scenario counterbalancing, so relative index `0` may resolve
to different scenarios for different subjects.

`--scenario` is a repeatable semantic filter over Contract scenario IDs. `--repetition` accepts a
1-based repetition number or inclusive range and is also repeatable. They may be combined with
subject and index filters, and are evaluated against the planned run's semantic coordinates rather
than its counterbalanced position. Consequently, `--scenario p1_a --repetition 1` names the same
scenario/repetition category for every subject even when each subject encounters that category at a
different global or subject-relative index. Selected runs retain their original global `run_index`,
and final execution order always follows deterministic Contract plan order rather than command-line
selector order.

`--preview` resolves the same filters and prints a compact subject/scenario/repetition breakdown
without constructing runtime adapters or creating result artifacts. It also inspects recorded
experiment deployment contexts for the selected subjects. A mismatch is reported as a warning;
preview then states whether strict mode would fail or `--apply-if-needed` would permit reconciliation.
Preview never mutates deployment state. Omit both selector types to execute the entire Contract plan.

A real execution first checks that every selected subject deployment records the exact selected
Experiment Package identity and declared integration provenance. Plain `acmm run` is
strict and aborts on deployment divergence. With `--apply-if-needed`, ACMM may apply the selected
package to only the subjects required by the selected run set. After that workflow returns, ACMM
re-reads persisted deployment state and runs the same strict deployment preflight again. The apply
return value alone is never sufficient; failure of the second preflight aborts before session
creation.

Once deployment provenance is ready, `acmm run` executes the same passive doctor logic used by
`acmm-infra <subject> doctor` and requires every selected runtime to be live and `READY` before
adapter construction or execution session creation.

`--start-if-needed` changes only the stopped-runtime case. After provenance succeeds, already
running subjects are still doctor-checked immediately; a running-but-unhealthy subject remains a
hard preflight failure. Stopped subjects are deferred and acquired lazily in selected run order by
default. ACMM starts the subject, doctor-checks it before use, and records lifecycle ownership in a
runtime lease. Runtime acquisition is attempted up to `--startup-max-attempts` times (default 3).
After failed attempts ACMM uses progressive cooldowns of 1s, 5s, 15s, 30s, then 60s for later
retries; the 60s delay is the cap. `--startup-timeout-seconds` remains the per-attempt startup
allowance. A failed ACMM-owned startup is reset before the next attempt, while a runtime manually
started during a cooldown can be discovered and readiness-checked on the next attempt.

`--prestart-selected-subjects` requires `--start-if-needed` and changes acquisition timing rather
than ownership. ACMM acquires and readiness-checks every selected subject before creating the
experiment execution session. If any selected runtime exhausts startup retries, ACMM releases
runtimes it already started and aborts without a scientific session. After successful pre-start,
normal subject-block release behavior is preserved: when execution moves to another subject, an
ACMM-started runtime may be stopped even though later subjects were already pre-started. Runtimes
that were already running before the command are never stopped by this policy. Any remaining
ACMM-owned runtime is released on normal completion, interruption, or handled execution failure.

Repository/runtime roots are not `acmm run` options. Repository location is infrastructure-owned,
and `ACMM_RUNTIME_ROOT` selects a non-default runtime/package store when needed. `--preview` stops
after local context inspection and does not apply experiment context, contact agent runtimes, or
start agent runtimes, even when `--apply-if-needed` or `--start-if-needed` is supplied.

`--capture-runtime-logs` is an opt-in diagnostic channel. For each subject lease used by the
execution, ACMM requests timestamped Docker logs bounded by the lease/acquisition start and captures
them immediately before subject release or execution cleanup. ACMM sanitizes only the persisted
diagnostic artifact: ANSI terminal presentation sequences are stripped, carriage-return/progress
effects are normalized into plain lines, backspace effects are resolved, and remaining non-text
control characters are removed. The running container and adapter evidence are unchanged.

The first plain UTF-8 segment is stored at `diagnostics/<subject_id>/container.log` inside the
execution directory; additional leases for the same subject use numbered files. Each segment has a
neighboring `<log-name>.meta.json` sidecar containing sanitizer identity, input/output byte counts,
and transformation counts so the diagnostic transformation is explicit. Runtime logs and metadata
remain outside the Run Record evidence model and are ignored by compliance evaluation and analysis
export. Capture is best-effort, so a Docker-log or file-write failure is reported as a warning
without changing execution validity.

`acmm run` requires one installed-package selector. `EXPERIMENT_ID` is accepted when one revision
is installed; otherwise use `EXPERIMENT_ID@SHA_PREFIX`. The selected package is reopened from its
immutable staged snapshot. Deployment context is then used strictly as preflight evidence and never
as an experiment-selection mechanism.


Ctrl+C exits with status 130 after persisting an interruption manifest and a human-readable
summary when an execution session has already started. No completion manifest is written.

## Current boundaries

Experiment-level execution intentionally keeps these boundaries:

- interrupted sessions are inspectable but cannot be resumed or exported as complete executions;
- CLI selection currently filters by planned-run index/range and subject ID, not by scenario or
  repetition directly;
- experiment runtime implementations come only from verified installed packages, while subject
  adapters remain qualified through the core subject catalog.


## Diagnostic environment snapshot request

`--capture-environment-snapshots` is a generic lifecycle hint. Package-defined snapshotting may add provider traffic and is disabled by default.
