# Versioned Analysis Plans

`acmm analyze` is the first-class orchestration surface for downstream ACMM statistical analysis. It
consumes an already-created deterministic analysis export; it never reads or mutates canonical
experiment/campaign acquisition artifacts directly.

```text
acmm analyze \
    <export-directory> \
    --plan analysis/plans/standard_v0_4.yaml
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

## Supported plan schemas

Analysis plans are versioned reproducibility inputs. Core validates the plan before running any R stage, resolves dependencies deterministically, and copies the exact plan into the immutable analysis result.

The repository includes these standard Campaign plans:

```text
analysis/plans/standard_v0_1.yaml
analysis/plans/standard_v0_2.yaml
analysis/plans/standard_v0_3.yaml
analysis/plans/standard_v0_4.yaml
```

`standard_v0_4.yaml` is the preferred Campaign starting point. It uses the strict population, 2,000 hierarchical bootstrap replicates, seed 12345, 95% confidence, PNG figures, compliance/dimension analysis, availability, generic trajectory summaries, and the tabular workbooks.

Provider Pressure uses a separate lightweight plan family. The preferred plan is:

```text
analysis/plans/provider_pressure_v0_3.yaml
```

The plan path does not silently change the requested population. Campaign plans must declare either `strict` or `available-valid` explicitly. Provider Pressure plans have no Campaign population policy.

Generic Campaign analysis kinds are:

- `requirement_estimates`
- `requirement_uncertainty`
- `dimension_scores`
- `dimension_uncertainty`
- `availability`
- `interaction`
- `trajectory`

Available report kinds are `compliance_workbook`, `availability_workbook`, and `interaction_workbook`.

Workbooks are tabular presentation artifacts. Figures are rendered by R as standalone files under `figures/` and can be selected independently through the plan.

## Plot modules

Plan schemas 0.2 through 0.6 expose generic R plot modules through `plots:`. Each requested plot is a
normal analysis stage with deterministic dependencies, progress output, logs, manifest provenance,
and hashed figure outputs. Schema 0.2 preserves the historical PNG+PDF pair. Schemas 0.3 through 0.6 default to PNG only and
allow `defaults.plot_formats` or a per-plot `formats` override using `png` and/or `pdf`. PDF is
therefore opt-in in the preferred standard plan.

Implemented plot kinds are:

- `run_duration`: selected-run duration distribution by subject;
- `requirement_compliance`: requirement estimates with optional bootstrap intervals;
- `dimension_scores`: one selected estimand/aggregation method with optional intervals;
- `dimension_profile` (schema 0.3): grouped-bar GOBPFR point-estimate profile for one estimand and aggregation method;
- `availability`: first-attempt versus final-after-retry availability by subject;
- `trajectory_run_metrics`: selected-run distribution for one generic trajectory metric;
- `trajectory_tool_usage`: historical cross-subject top-N raw tool-call volume view;
- `trajectory_error_rate` (schema 0.4): per-run tool-result error-rate distribution by subject;
- `trajectory_tool_composition` (schema 0.4): separate per-subject top-N raw tool-name shares. Raw
  names are intentionally not canonicalized across adapters.
- `provider_pressure_pacing` (schema 0.5): pacing burden by checkpoint and recorded pacing reason;
- `provider_pressure_admission_waits` (schema 0.5): per-run total action-admission wait distribution
  by subject;
- `provider_pressure_utilization_envelope` (schema 0.6): per-run maximum recorded content-request
  utilization across pressure-event order, expressed as a percentage of the recorded operational
  threshold;
- `provider_pressure_utilization_by_subject` (schema 0.6): subject-wise distributions of per-run
  maximum requests/hour and requests/minute utilization;
- `provider_pressure_wait_vs_actions` (schema 0.6): observed run-level provider-managed action count
  versus total admission wait, with no fitted model. The historical `provider_pressure_feature_scores`
  plot remains supported but is not in the preferred pressure plan because saturated feature scores
  can be visually uninformative.

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

Trajectory analysis intentionally limits itself to structurally defensible facts: tool-call
intensity, distinct-tool breadth, result/error counts and rates, repeated tool-name usage,
consecutive repeated tool-name usage, and call/result matching by recorded IDs. It does not infer
recovery, verification, mutation, or redundancy semantics; those require an explicit
classification/sequence contract rather than experiment-specific assumptions hidden in Core.

Campaign export schema 0.2 remains valid for the pre-trajectory statistical modules, but requesting
`trajectory` (directly or through a trajectory plot) against a 0.2 export is rejected before R
runtime work begins.

## Provider Pressure analysis

Provider Pressure uses a separate lightweight analysis family with no experiment population policy.
The preferred plan is `analysis/plans/provider_pressure_v0_3.yaml`. It requests the compact pressure
summary plus four descriptive figures: maximum recorded provider utilization across run
occurrences, utilization distributions by subject, per-run admission-wait burden, and admission wait
versus provider-managed action count. The utilization plots use the recorded operational-threshold
fractions directly; they do not infer a rate-limit wall or a latent provider state. Historical pacing
and learned-feature plots remain supported by earlier plans, while feature-state, signal, decision,
and pacing-event tables remain exported for audit and follow-up work.

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
