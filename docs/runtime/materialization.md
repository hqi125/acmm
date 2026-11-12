# Campaign materialization

## Purpose

Some experiment apparatus resources are expensive to construct and can safely serve more than one logical run when the package can restore their trusted baseline between observations. Campaign materialization lets an Experiment Package declare that locality without teaching Core about a provider such as GitHub.

Materialization is a single outer scheduling layer. A package declares `campaign.materialization.group_by` in Experiment Package schema v0.4 using one or more planned-run coordinates: `run_index`, `subject_id`, `scenario_id`, or `repetition`.

Core freezes the resulting materialization key on every Campaign v0.2 logical run. This makes resume/recovery independent of later package code changes.

## Scheduling invariant

For each materialization slice, Core always executes selected runs subject-major according to Contract subject declaration order. The package chooses only the outer grouping; it does not receive an arbitrary nested scheduling DSL.

Conceptually:

```text
for materialization slice in deterministic slice order:
    materialization_initialize(slice)

    for subject in Contract order:
        execute the selected runs for that subject in deterministic run-index order

    materialization_finalize(slice)
```

A package that does not declare campaign materialization receives the historical behavior: `group_by: [run_index]`, so every logical run forms its own materialization slice.

## Runtime hooks

A runtime lifecycle may optionally implement both:

- `materialization_initialize(session_handle, context)`;
- `materialization_finalize(session_handle, context, handle, outcome)`.

The returned materialization handle is passed read-only through each `runtime_run_context` in the slice. Existing `run_prepare`, `run_observe`, and `run_finalize` hooks remain the per-run evidence boundary.

Implementing only one materialization hook is invalid. Packages that do not implement either hook remain compatible.

## Failure semantics

Materialization finalization is called when Core leaves a completed slice and also during interruption/failure cleanup. Packages remain responsible for provider-specific taint, forensic capture, restoration, and resource retirement.

A package should stop execution rather than silently continue when shared state cannot be restored to a trusted baseline. Core records the materialization boundary and preserves already persisted run evidence; Campaign recovery can later continue unresolved logical runs in a fresh execution/materialization.

## Campaign compatibility

Campaign v0.2 freezes:

- materialization `group_by`;
- `subject_major_within_slice: true`;
- materialization slice count;
- each run's `materialization_key`.

Campaign v0.1 remains readable and retains its original design digest. New campaigns use v0.2.
