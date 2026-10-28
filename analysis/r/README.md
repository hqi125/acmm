# ACMM R Analysis

This directory is the downstream statistical-analysis subsystem for ACMM exports.

The experiment system remains authoritative for acquisition and canonical evidence. R consumes only
validated analysis exports produced by `acmm export`; it never mutates Contracts, Run Records,
evidence, execution manifests, or exported CSV files.

## Design boundary

The analysis layer currently implements:

- validated export ingestion;
- construction of the selected valid statistical population;
- planned-slot availability analysis with first-attempt/retry diagnostics;
- subject/scenario/within-repetition-position availability summaries;
- scenario-to-slot hierarchical availability intervals;
- extreme-case opportunity-level missing-outcome sensitivity envelopes;
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

Compliance bootstrap intervals under `available-valid` are conditional on the observed valid
population. They quantify sampling variation among available observations but do not model the unknown
outcomes of missing planned runs. The general availability stage addresses this separately: it retains
the planned-slot denominator, reports first-attempt/retry acquisition behavior and imbalance, and
constructs an explicit all-missing-violation to all-missing-compliant opportunity-level sensitivity
envelope. The envelope is not a confidence interval and does not assume missing-at-random. See
`docs/analysis/availability.md`.

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

General availability analysis:

```text
Rscript analysis/r/scripts/analyze_availability.R \
    exports/<experiment_id>/execution_<execution_id> \
    2000 \
    12345 \
    0.95 \
    exports/<experiment_id>/execution_<execution_id>/analysis_output \
    --population-policy=available-valid
```

The availability script writes slot, cell, selected-position, failure-mode, retry/attempt, interval,
and opportunity-level sensitivity tables under `analysis_output/data/availability/`. For a fully
resolved execution, `strict` is also valid. The population option remains available on the standalone
R scripts for diagnostics; routine repository analysis resolves the population automatically.

The repository wrapper asks the compliance R stages to write normalized tables under
`analysis_output/data/compliance/` and captures their readable text output under
`analysis_output/logs/compliance/`:

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

No population option is required for routine use. The wrapper first attempts the strict population
semantically: if the export reports that every selected planned run resolved, strict analysis is used.
Otherwise it emits a warning and automatically uses available-valid compliance analysis. This
fallback is specific to incomplete acquisition; integrity, schema, dependency, or estimator failures
remain fatal.

The wrapper does not implement statistics and is not part of the `acmm` CLI. It follows repository
storage conventions to:

1. run `acmm export` when the canonical export does not already exist;
2. verify and safely reuse a matching existing export when rerunning after an analysis-stage failure;
3. resolve strict versus available-valid compliance population automatically;
4. run requirement point estimates, requirement uncertainty, dimension point scores, and dimension
   uncertainty in order;
5. run the general availability analysis for every structurally valid execution;
6. store derived CSV tables under `analysis_output/data/<layer>/` and text diagnostics under
   `analysis_output/logs/<layer>/`;
7. build `analysis_report.xlsx` and `availability_analysis.xlsx` directly under `analysis_output/`;
   and
8. run/build registered supplementary analyses without changing the core compliance estimators.

The current analysis protocol is intentionally fixed in this research tool at 2,000 hierarchical
bootstrap replicates, seed 12,345, and confidence level 0.95. Statistical calculations remain entirely
inside the R analysis subsystem.

## Registered supplementary analysis

The core analysis workflow remains intentionally stable and writes
`analysis_output/analysis_report.xlsx`. Availability is a general companion layer and writes
`analysis_output/availability_analysis.xlsx` for every experiment. Experiments may additionally
register a separate supplementary analysis
when their scientific design requires estimands that do not belong in the general ACMM workbook.
Supplementary outputs must not silently change the core requirement/dimension estimators.

Interaction analysis is implemented as a generic, experiment-registered supplementary capability.
The experiment supplies a small `interaction_analysis.yaml` design specification that defines the two
factors, their environment-configuration bindings, the primary contrast text, and any figures worth
generating. The R interaction engine and workbook consume that specification instead of hard-coding
a particular experiment.

`controlled_recovery_boundary_interaction_v1` registers the first interaction design. Its R stage
writes derived tables under `analysis_output/data/interaction/` and figures under
`analysis_output/figures/interaction/`:

```text
interaction_population_summary.csv
interaction_population_cell_coverage.csv
interaction_population_missing_runs.csv
interaction_cell_estimates.csv
interaction_simple_effects.csv
interaction_contrasts.csv
interaction_figures.csv
figures/interaction/*.png
```

The repository wrapper then builds a separate `interaction_analysis.xlsx` workbook and embeds the
R-generated figures while retaining the standalone PNG files for reuse in reports.

For Experiment 3, factor A is ordinary-write outcome (`success -> failure`) and factor B is
authorized-recovery availability (`unavailable -> available`). Cell compliance is estimated at the
run level. The primary interaction estimand is the risk-difference difference-in-differences:

```text
interaction =
    [p(failure, available) - p(failure, unavailable)]
  - [p(success, available) - p(success, unavailable)]
```

This directly asks whether recovery availability changes compliance differently after ordinary-write
failure than after ordinary-write success. Goal is intentionally not factorial-estimable because the
failure/no-recovery condition has no Goal opportunity by design; the three applicable Goal cells are
still reported descriptively and any estimable pairwise simple effects remain available.

Interaction uncertainty uses a percentile bootstrap over Experiment 3 repetition blocks. Each sampled
repetition carries its observed factorial cells together so short-timescale/common-order variation is
not discarded. When all observed run-level outcomes for a requirement are identical, the point
interaction remains reportable but the nonparametric bootstrap interval is marked
`degenerate_observed_outcome` rather than presenting a zero-width interval as strong uncertainty
evidence.

Figures are optional and are generated only when the registered interaction design requests them.
When figures are useful, they are generated in R with base graphics so the statistical population and
visualization path share the same filtering and factor construction. Excel-native charts remain an
acceptable fallback for future analyses, but statistical figures should prefer the R path when
practical.

An experiment does not receive interaction analysis merely because it has multiple scenarios or
dimensions. It must register a scientifically justified interaction design. This is why Experiment 4
receives the general compliance and availability layers but no interaction workbook.
