# Experiment/Analysis Boundary

ACMM separates experimental data acquisition from statistical interpretation.

The intended pipeline is:

```text
Experiment Contract
        |
        v
ACMM experiment automation
        |
        +-- exact Contract snapshots
        +-- execution-session manifests
        +-- Run Records
        +-- raw/normalized evidence
        |
        v
deterministic analysis export
        |
        +-- CSV tables
        |
        v
separate statistical workspace
        |
        +-- R / RStudio
        +-- compliance proportions
        +-- dimension scores
        +-- confidence intervals
        +-- statistical models
        +-- figures and tables
```

## Canonical data

Run Records, execution-session metadata, evidence, and Contract snapshots are the canonical
experimental source of truth.

CSV is the planned interchange format for downstream statistical analysis, not the
canonical storage format for the experiment itself.

## ACMM Python boundary

`src/acmm` may contain deterministic data-export code because export is a mechanical
projection of validated experimental facts.

It must not contain research-level statistical interpretation merely to support the
analysis workflow.

In particular, the experiment automation should not precompute:

```text
p-hat estimators
S_D dimension scores
confidence intervals
hypothesis tests
regression/statistical models
plots
```

Those belong to a separate downstream analysis workspace.

## One-way communication

The intended dependency direction is:

```text
experiment results -> CSV export -> statistical analysis
```

Statistical code must not mutate canonical experiment results.

If analysis reveals that an additional experimental variable is required, the Experiment
Contract/harness/export definition should be revised explicitly and the appropriate data
should be recollected or re-exported.

## R integration

The initial preferred workflow is ordinary CSV interchange with R/RStudio.

Embedding R inside the Python experiment environment or using Python-to-R APIs is not
required for the core architecture and should be considered only if a later workflow
demonstrates a concrete benefit.

The statistical workspace should be able to run independently from `src/acmm` once its
versioned CSV inputs have been produced.
## Implemented CSV export

The mechanical export boundary is now implemented by the scoped `acmm export run` and
`acmm export campaign` commands. `export run` preserves the existing completed-execution schema 0.1
path. `export campaign` uses schema 0.3 (with legacy schema 0.2 still readable) and validates a frozen Campaign together with one immutable
merge so acquisition history and final adjudicated observations remain distinct but jointly
traceable.

```text
runs.csv       one row per realized attempt, including invalid retries
judgments.csv  one row per recorded opportunity judgment
```

The export keeps `selected`, `validity`, and `termination` explicit so downstream analysis can
choose its population without the Python layer silently dropping attempts. It joins only stable
Contract metadata such as requirement ID, dimension, check ID, and evaluator; it does not add
methodological denominator flags or statistical quantities.

See `docs/analysis/export.md` for the exact table and determinism rules.


## Implemented R analysis workspace

The first downstream statistical layer now lives under `analysis/r/`. It is intentionally
separate from `src/acmm` and consumes only deterministic export artifacts.

The loader dispatches by export schema and verifies the manifest, CSV SHA-256 values, exact table
columns, typed field syntax, retry/selection consistency, run-to-judgment bindings, and population
completeness before an estimator is allowed to run. Schema 0.1 retains the execution path; Campaign schemas 0.2/0.3
add an explicit Campaign planned-run table so an unattempted logical slot remains visible in the
denominator. Invalid/non-selected attempts remain available as diagnostics but are not part of the
selected valid statistical population.

The initial estimator surface is requirement-level and implements three alternatives already
defined by ACMM quantification work:

```text
opportunity-weighted compliance
run-weighted compliance
scenario-weighted compliance
```

`unadjudicable` contributes to coverage but not the compliance-rate denominator;
`not_applicable` is excluded from both. A zero estimable denominator produces `NA` rather than an
invented numerical result.

The second statistical milestone adds uncertainty estimation without changing the underlying
estimands. `interval_acmm_hierarchical()` performs scenario-to-run cluster resampling and percentile
intervals while retaining opportunities inside sampled runs. It refuses scenario-generalized
intervals when fewer than two scenario clusters are observed. `interval_acmm_wilson()` is available
only as an explicitly opted-in independent-binomial opportunity baseline.

Dimension-level `S_D` aggregation is implemented separately from the requirement-level estimators.
Hierarchical dimension uncertainty resamples scenario/run clusters jointly across the requirements in
one dimension, recomputes requirement estimands inside each replicate, and then recomputes `S_D`.
Hypothesis tests, fitted statistical models, semantic tool-action interpretation, and custom requirement weights remain outside the current
analysis scope.

See `analysis/r/README.md` for setup, validation rules, estimators, and uncertainty commands.

## R dimension aggregation

The downstream R analysis layer may aggregate validated requirement-level compliance estimates into
dimension-level `S_D` point scores. The aggregation method must remain explicit: arithmetic,
geometric, harmonic, or minimum. The initial implementation uses equal requirement weights only and
refuses to renormalize over missing requirement estimates. The same scenario-to-run hierarchical
bootstrap can now be propagated through `S_D`; dimension intervals are therefore based on replicate
dimension scores, not on separately aggregated requirement interval endpoints.

## Availability as a separate measurement layer

Execution availability is now formalized downstream without changing the canonical Run Record or
turning invalid acquisition into compliance failure. The availability layer treats the selected
planned run slot as its denominator and reports whether the declared run protocol produced one
selected valid attempt.

This creates a deliberate separation:

```text
planned selected slot
        |
        +-- unavailable -> acquisition loss, operational failure diagnostics
        |
        `-- available valid run
                 |
                 `-- compliance opportunity -> compliant / violation /
                                             unadjudicable / not_applicable
```

The R availability stage reports subject/scenario/selected-position acquisition rates,
first-attempt and retry behavior, scenario-to-slot bootstrap intervals, and an explicit extreme-case
missing-outcome sensitivity envelope. It writes normalized CSVs plus a separate
`availability_analysis.xlsx` presentation workbook. The core `analysis_report.xlsx` remains focused
on observed valid compliance estimands.

Recorded `termination` and `failure_kind` fields are treated as operational surfaces rather than
automatic causal labels. A harness-visible failure can still originate in subject behavior, so causal
attribution requires evidence-backed diagnosis outside the availability rate itself.

See `docs/analysis/availability.md` for the formal definitions and reporting rules.

## First-class generic analysis orchestration

The statistical implementation remains in the Core repository's `analysis/r` workspace.
`acmm analyze <export-dir> --plan <plan.yaml>` adds only generic orchestration/provenance in
`src/acmm`: schema validation, dependency resolution, subprocess execution, progress forwarding,
atomic publication, and artifact hashing. It does not move statistical estimators into the Python
experiment runtime.

Experiment-specific scientific choices are expressed as downstream plan/design data. For example, a
2 x 2 interaction design may be referenced by an analysis plan, while the executable interaction
implementation remains generic Core R code. No post-acquisition analysis code or plan is required to
be added to a frozen Experiment Package.

