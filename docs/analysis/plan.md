# Versioned Analysis Plans

`acmm analyze` is the first-class orchestration surface for downstream ACMM statistical analysis. It
consumes an already-created deterministic analysis export; it never reads or mutates canonical
experiment/campaign acquisition artifacts directly.

```text
acmm analyze \
    ~/acmm_generated/exports/<experiment_id>/campaign_<campaign_id>/merge_<merge_id> \
    --plan analysis/plans/standard_v0_2.yaml
```

The default result root is `<export>/analysis_output`. Each run is published into an immutable
fingerprinted directory:

```text
analysis_output/
└── analysis_<fingerprint-prefix>/
    ├── analysis_plan.yaml
    ├── analysis_manifest.yaml
    ├── data/
    ├── logs/
    ├── figures/
    └── *.xlsx
```

A completed analysis directory is never overwritten implicitly. Work is performed in a staging
directory and published atomically. A failed/interrupted analysis is retained as
`analysis_<id>.failed` for diagnosis.

## Plan schemas

Plan schema 0.1 remains supported for the original requirement/dimension/availability/interaction
workflow. Plan schema 0.2 is additive: it adds normalized tool-trajectory analysis and selectable
R-rendered plots while retaining the 0.1 analysis/report semantics.

The repository provides:

```text
analysis/plans/standard_v0_1.yaml
analysis/plans/standard_v0_2.yaml
```

The v0.2 standard plan is the preferred campaign-analysis starting point:

```yaml
schema_version: '0.2'
population:
  policy: strict
defaults:
  bootstrap:
    replicates: 2000
    seed: 12345
    confidence_level: 0.95
analyses:
  - kind: requirement_estimates
  - kind: requirement_uncertainty
  - kind: dimension_scores
  - kind: dimension_uncertainty
  - kind: availability
  - kind: trajectory
reports:
  - kind: compliance_workbook
  - kind: availability_workbook
plots:
  - kind: run_duration
  - kind: requirement_compliance
    estimand: scenario
    include_intervals: true
  - kind: dimension_scores
    estimand: opportunity
    aggregation_method: arithmetic
    include_intervals: true
  - kind: availability
  - kind: trajectory_run_metrics
    metric: tool_calls
  - kind: trajectory_tool_usage
    top_n: 12
```

`population.policy` must be either `strict` or `available-valid`; the plan path has no automatic
fallback. Bootstrap parameters may be overridden on `requirement_uncertainty`,
`dimension_uncertainty`, `availability`, or `interaction` analysis entries.

The generic analysis kinds are:

- `requirement_estimates`
- `requirement_uncertainty`
- `dimension_scores`
- `dimension_uncertainty`
- `availability`
- `interaction`
- `trajectory` (plan schema 0.2; requires Campaign export schema 0.3)

The report kinds are `compliance_workbook`, `availability_workbook`, and `interaction_workbook`.
Workbooks are intentionally tabular/presentation-oriented. Core does not embed analysis charts into
the Excel files; selectable figures are rendered by R as standalone PNG and PDF artifacts.

## Plot modules

Plan schema 0.2 exposes generic R plot modules through `plots:`. Each requested plot is a normal
analysis stage with deterministic dependencies, progress output, logs, manifest provenance, and
hashed PNG/PDF outputs under `figures/`.

Implemented plot kinds are:

- `run_duration`: selected-run duration distribution by subject;
- `requirement_compliance`: requirement estimates with optional bootstrap intervals;
- `dimension_scores`: one selected estimand/aggregation method with optional intervals;
- `availability`: first-attempt versus final-after-retry availability by subject;
- `trajectory_run_metrics`: selected-run distribution for one generic trajectory metric;
- `trajectory_tool_usage`: top-N tool-call volume by subject.

Trajectory run metrics currently selectable for plotting are `tool_calls`, `distinct_tools`,
`error_rate`, `repeated_calls`, and `unmatched_calls`. `repeated_calls` means repeated use of a tool
name after its first use within a run; it is descriptive and is not automatically interpreted as
redundant behavior. `unmatched_calls` visualizes call/result ID matching diagnostics rather than a
semantic recovery score.

A plot kind may currently appear at most once in one plan. Different parameterizations can be run as
distinct immutable plans when needed.

## Trajectory analysis

`trajectory` consumes normalized `tool_calls.csv` and `tool_results.csv` from Campaign export schema
0.3. It produces generic descriptive tables for selected scientifically valid observations:

```text
data/trajectory/trajectory_run_metrics.csv
data/trajectory/trajectory_subject_summary.csv
data/trajectory/trajectory_tool_usage.csv
```

The first trajectory version intentionally limits itself to structurally defensible facts: tool-call
intensity, distinct-tool breadth, result/error counts and rates, repeated tool-name usage,
consecutive repeated tool-name usage, and call/result matching by recorded IDs. It does not infer
recovery, verification, mutation, or redundancy semantics. Those require an explicit future
classification/sequence contract rather than experiment-specific assumptions hidden in Core.

Campaign export schema 0.2 remains valid for the pre-trajectory statistical modules, but requesting
`trajectory` (directly or through a trajectory plot) against a 0.2 export is rejected before R
runtime work begins.

## Dependencies

Dependencies are resolved deterministically. For example, `dimension_uncertainty` causes
`requirement_estimates` and `dimension_scores` to run first. A compliance workbook requires all four
compliance/dimension stages. An availability workbook requires availability analysis. An interaction
workbook requires an explicit `interaction` analysis because Core cannot invent its design.
Trajectory plots depend on `trajectory`; other plots depend on the tables/intervals they display.

## Interaction design

Experiment-specific scientific contrasts remain declarative inputs, not experiment-specific Core
code. A plan may reference a downstream interaction design relative to the plan file:

```yaml
analyses:
  - kind: interaction
    design: interaction_design.yaml
```

The design is copied into the immutable analysis result and hashed into the analysis fingerprint.
Core's existing generic interaction R implementation then validates and executes it.

## Provenance

`analysis_manifest.yaml` records the export-manifest hash and source identity, exact plan hash,
resolved dependency order, requested plot parameters, referenced-input hashes, implementation-file
hashes and aggregate digest, Python/ACMM/R/XlsxWriter versions, required R-package versions, stage
outputs, and SHA-256/byte size of every published artifact except the manifest itself.

The analysis fingerprint incorporates the export, plan, referenced inputs, implementation digest,
and runtime versions. A software or methodological change therefore creates a distinct analysis ID.

## Progress output

The CLI emits one line for broad phases and one start/completion pair per resolved stage. Long R
stages emit only sparse `ACMM_PROGRESS:` milestones that are forwarded to the console while ordinary
R output remains in the stage log. Plot rendering follows the same rule. This keeps heavy work
visible without streaming tables, per-run records, or bootstrap iterations.
