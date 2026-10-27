# ACMM R Analysis

This directory is the downstream statistical-analysis subsystem for ACMM exports.

The experiment system remains authoritative for acquisition and canonical evidence. R consumes only
validated analysis exports produced by `acmm export`; it never mutates Contracts, Run Records,
evidence, execution manifests, or exported CSV files.

## Design boundary

The analysis layer currently implements:

- validated export ingestion;
- construction of the selected valid statistical population;
- opportunity-, run-, and scenario-weighted compliance estimands;
- coverage for the same three weighting hierarchies;
- explicit Wilson score intervals for independent-binomial opportunity reporting;
- scenario-to-run hierarchical percentile bootstrap intervals;
- explicit dimension-level `S_D` aggregation with arithmetic, geometric, harmonic, and minimum
  compensation policies;
- cluster-aware hierarchical uncertainty intervals propagated through `S_D`; and
- machine-readable CSV tables for each analysis stage.

Requirement-level grouping remains the estimator default. Dimension scores are a separate, explicit
aggregation step so the compensation choice is always visible. Equal requirement weights are the only
weighting policy implemented in this stage.

Hypothesis tests, statistical models, plots, and custom requirement weights are not implemented in
this stage.

## Dependencies

The analysis layer requires three CRAN packages:

```r
install.packages(c("digest", "jsonlite", "yaml"))
```

The code never installs packages automatically. Missing dependencies stop analysis with an explicit
error.

## Input validation

`load_acmm_export()` validates the input before any estimator runs. The v0.1 checks include:

- export-manifest schema and source binding;
- SHA-256 of `runs.csv` and `judgments.csv`;
- exact CSV column order;
- manifest row counts;
- UUID, SHA-256, timestamp, integer, number, and boolean syntax;
- JSON object/array cells;
- execution-level count consistency;
- retry attempt ordering and selected-attempt rules;
- validity/failure consistency;
- run-to-judgment foreign-key bindings;
- judgment counts per realized attempt;
- opportunity, requirement, dimension, check, and evaluator consistency.

By default, analysis uses the `strict` population policy and refuses an execution whose selected plan
is not fully resolved. `available-valid` is an explicit alternative for postmortem and sensitivity
analysis: it retains the original selected-plan denominator, analyzes only selected scientifically
valid runs, and reports exact missing planned slots plus per-subject/scenario acquisition coverage.
Invalid attempts remain available in `population$excluded_attempts`; they are never converted into
compliance violations or silently deleted.

Bootstrap intervals under `available-valid` are conditional on the observed valid population. They
quantify sampling variation among available observations but do not model the unknown outcomes of
missing planned runs. If missingness is related to subject behavior or experimental condition, pooled
estimates may be biased; inspect `population_cell_coverage.csv` and
`population_missing_runs.csv` and use an explicit sensitivity analysis rather than assuming the
missing observations are random.

## Compliance semantics

For an analysis scope:

```text
C = compliant opportunities
V = violation opportunities
U = applicable but unadjudicable opportunities

p_hat    = C / (C + V)
coverage = (C + V) / (C + V + U)
```

`not_applicable` is excluded from both denominators. A zero denominator produces `NA`, never an
invented zero.

Opportunity weighting pools opportunities. Run weighting computes an estimable ratio per run and then
averages runs equally. Scenario weighting first averages runs within a scenario and then averages
estimable scenarios equally.


## Dimension aggregation

`aggregate_acmm_dimensions()` consumes requirement-level estimate tables and computes `S_D` within
`subject_id + dimension` groups. `estimate_acmm_dimension_scores()` is the convenience wrapper that
first computes all three requirement-level estimands and then applies the same dimension aggregation.

The implemented equal-weight score families are:

```text
arithmetic: mean(p_i)
geometric:  product(p_i)^(1 / m)
harmonic:   m / sum(1 / p_i)
minimum:    min(p_i)
```

where `m` is the number of requirements in the dimension and `p_i` is the selected requirement-level
`p_hat`. Geometric and harmonic scores are exactly zero when any contributing score is zero.

Every result records `aggregation_method = arithmetic|geometric|harmonic|minimum` and
`weighting = equal`; there is no unnamed default compensation policy.

Dimension aggregation is deliberately strict about missing requirement estimates. If any contributing
requirement has `p_hat = NA`, `s_d` is `NA` and `aggregation_status` is
`incomplete_requirement_estimates`. The analysis layer does not silently drop that requirement and
renormalize the remaining scores.

## Confidence intervals

ACMM does not attach one universal confidence interval to every compliance estimate. The interval
method must match the sampling/independence assumptions of the estimand.

### Hierarchical bootstrap

`interval_acmm_hierarchical()` is the default cluster-aware uncertainty method in this stage. For each
requirement-level group it:

1. resamples observed scenarios with replacement;
2. resamples observed runs with replacement inside each sampled scenario;
3. retains all opportunities belonging to each sampled run as a cluster;
4. recomputes opportunity-, run-, and scenario-weighted estimates;
5. forms percentile intervals from the finite bootstrap replicates.

The caller must provide an explicit random seed. The function restores the caller's R random-number
state after it completes.

At least two observed scenario clusters are required for a scenario-generalized hierarchical interval.
With fewer than two scenarios, interval bounds are `NA` and the corresponding status is
`insufficient_scenario_clusters`; ACMM does not manufacture a zero-width interval from one scenario.

Example API call:

```r
intervals <- interval_acmm_hierarchical(
    population,
    confidenceLevel = 0.95,
    bootstrapReplicates = 2000,
    seed = 12345
)
```

Each returned table records the scenario/run cluster counts, requested bootstrap count, finite
replicate count, interval bounds, and a status for `p_hat` and coverage.

### Dimension-score hierarchical bootstrap

`interval_acmm_dimension_scores_hierarchical()` propagates the same scenario-to-run resampling
through dimension aggregation. Each replicate jointly resamples all requirement judgments in one
`subject_id + dimension` group, recomputes the selected requirement-level estimand, and then
recomputes arithmetic, geometric, harmonic, and minimum `S_D`. Percentile bounds are formed from
the resulting finite `S_D` replicates.

This joint recomputation preserves observed dependence between requirements inside a dimension. ACMM
does not construct a dimension interval by aggregating lower and upper requirement-interval endpoints.
The same minimum-two-scenario rule applies; a replicate whose dimension score is not estimable is
excluded from the percentile calculation and reported through the finite replicate count.

Example API call:

```r
dimensionIntervals <- interval_acmm_dimension_scores_hierarchical(
    population,
    confidenceLevel = 0.95,
    bootstrapReplicates = 2000,
    seed = 12345
)
```

### Wilson independent-binomial baseline

`interval_acmm_wilson()` implements the Wilson score interval for opportunity-level reporting only.
It is deliberately opt-in:

```r
intervals <- interval_acmm_wilson(
    population,
    confidenceLevel = 0.95,
    assumeIndependent = TRUE
)
```

Without `assumeIndependent = TRUE`, the function stops with `acmm_assumption_error`. This prevents a
pooled opportunity-level binomial interval from being silently treated as valid when opportunities are
clustered within runs or scenarios.

## Tests

From the repository root:

```text
Rscript analysis/r/tests/run_tests.R
```

The test runner creates its own temporary exports. It does not depend on the magic-number result tree.
It also checks Wilson reference values, single-scenario bootstrap refusal, deterministic seeded
hierarchical resampling, and preservation of the caller's R random-number state.

## Analyze an export

Basic point estimates:

```text
Rscript analysis/r/scripts/analyze_export.R \
    exports/<experiment_id>/execution_<execution_id>
```

Hierarchical uncertainty analysis:

```text
Rscript analysis/r/scripts/analyze_uncertainty.R \
    exports/<experiment_id>/execution_<execution_id> \
    2000 \
    12345 \
    0.95
```

The uncertainty script requires the export directory, bootstrap replicate count, and seed. The
confidence level is optional and defaults to `0.95`.


Dimension-score analysis:

```text
Rscript analysis/r/scripts/analyze_dimensions.R \
    exports/<experiment_id>/execution_<execution_id>
```

This reports arithmetic, geometric, harmonic, and minimum `S_D` point values separately for the
opportunity-, run-, and scenario-weighted requirement estimands.

Dimension-score hierarchical uncertainty:

```text
Rscript analysis/r/scripts/analyze_dimension_uncertainty.R \
    exports/<experiment_id>/execution_<execution_id> \
    2000 \
    12345 \
    0.95
```

The repository wrapper also asks each R stage to write a normalized CSV table beside its text report:

```text
requirement_estimates.csv
requirement_intervals.csv
dimension_scores.csv
dimension_intervals.csv
```

Each table includes an `estimand` column (`opportunity`, `run`, or `scenario`) so it can be filtered
or imported directly into RStudio or Excel. `tools/analyze_execution.py` also uses these tables as the
authoritative inputs to its final `analysis_report.xlsx` presentation stage.


## Repository workflow wrapper

For routine processing of one completed execution, the repository also provides a thin Python
orchestration tool:

```text
python tools/analyze_execution.py \
    results/<experiment_id>/execution_<execution_id>
```

For an incomplete execution that should remain analyzable as an observed valid population:

```text
python tools/analyze_execution.py \
    results/<experiment_id>/execution_<execution_id> \
    --population-policy available-valid
```

The wrapper does not implement statistics and is not part of the `acmm` CLI. It follows repository
storage conventions to:

1. run `acmm export` when the canonical export does not already exist;
2. verify and safely reuse a matching existing export when rerunning after an analysis-stage failure;
3. run requirement point estimates, requirement uncertainty, dimension point scores, and dimension
   uncertainty in order;
4. stop immediately when any stage fails;
5. store both human-readable text output and normalized analysis CSV tables under
   `<export-directory>/analysis_output/`; and
6. build `analysis_report.xlsx` from the normalized analysis tables plus `runs.csv` and
   `judgments.csv`.

The current analysis protocol is intentionally fixed in this research tool at 2,000 hierarchical
bootstrap replicates, seed 12,345, and confidence level 0.95. Statistical calculations remain entirely
inside the R analysis subsystem.
