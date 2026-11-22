# Versioned Analysis Plans

`acmm analyze` is the first-class orchestration surface for downstream ACMM statistical analysis. It
consumes an already-created deterministic analysis export; it never reads or mutates canonical
experiment/campaign acquisition artifacts directly.

```text
acmm analyze \
    ~/acmm_generated/exports/<experiment_id>/campaign_<campaign_id>/merge_<merge_id> \
    --plan analysis/plans/standard_v0_1.yaml
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

## Plan schema 0.1

A plan makes methodological choices explicit rather than allowing the CLI to infer them:

```yaml
schema_version: '0.1'
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
reports:
  - kind: compliance_workbook
  - kind: availability_workbook
```

`population.policy` must be either `strict` or `available-valid`; the new plan path has no automatic
fallback. Bootstrap parameters may be overridden on `requirement_uncertainty`,
`dimension_uncertainty`, `availability`, or `interaction` analysis entries.

The implemented generic analysis kinds are:

- `requirement_estimates`
- `requirement_uncertainty`
- `dimension_scores`
- `dimension_uncertainty`
- `availability`
- `interaction`

The implemented report kinds are `compliance_workbook`, `availability_workbook`, and
`interaction_workbook`.

Dependencies are resolved deterministically. For example, `dimension_uncertainty` causes
`requirement_estimates` and `dimension_scores` to run first. A compliance workbook requires all four
compliance/dimension stages. An availability workbook requires availability analysis. An interaction
workbook requires an explicit `interaction` analysis because Core cannot invent its design.

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
resolved dependency order, referenced-input hashes, implementation-file hashes and aggregate digest,
Python/ACMM/R/XlsxWriter versions, required R-package versions, stage outputs, and SHA-256/byte size of
every published artifact except the manifest itself.

The analysis fingerprint incorporates the export, plan, referenced inputs, implementation digest,
and runtime versions. A software or methodological change therefore creates a distinct analysis ID.

## Progress output

The CLI emits one line for broad phases and one start/completion pair per resolved stage. Long R
stages may emit sparse `ACMM_PROGRESS:` markers (for example before and after a bootstrap) that are
forwarded to the console while all ordinary R output stays in the stage log. This keeps long work
visible without streaming tables or per-run noise.
