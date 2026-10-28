# Supplementary Interaction Analysis

ACMM keeps the general requirement/dimension analysis separate from experiment-specific interaction
analysis. The core workflow writes `analysis_report.xlsx`; a registered supplementary analysis may
write its own workbook and figures without adding experiment-specific sheets to the core report.

## Experiment 3

`controlled_recovery_boundary_interaction_v1` is the first registered supplementary analysis. Its
factorial design has two run-level factors:

- factor A: ordinary-write outcome, `success -> failure`;
- factor B: authorized-recovery availability, `unavailable -> available`.

For each subject and requirement, the interaction stage estimates observed run-level compliance in the
four cells. Its primary contrast is the risk-difference difference-in-differences:

```text
[p(failure, available) - p(failure, unavailable)]
    -
[p(success, available) - p(success, unavailable)]
```

A positive value means recovery availability improves compliance more under ordinary-write failure
than under success. A negative value means the opposite. Zero means the observed recovery-availability
effect is the same in the two write-outcome strata.

The stage also reports both recovery-availability simple effects and both ordinary-write-outcome simple
effects so the interaction does not need to be interpreted in isolation.

Goal does not have a complete 2 x 2 design because the failure/no-recovery cell intentionally has no
Goal opportunity. ACMM does not invent a Goal outcome for that cell. Goal cell rates and estimable
pairwise effects remain descriptive, while the four-cell interaction status is
`not_estimable_incomplete_factorial`.

## Uncertainty

Interaction uncertainty uses Experiment 3's repetition index as a block. A bootstrap replicate samples
observed repetition blocks with replacement and carries the cells observed in each sampled repetition
together. The interaction is then recomputed from the sampled cell means. This preserves short-timescale
variation that could be shared by the four interleaved conditions within a repetition.

The bootstrap is conditional on the available-valid population selected by the ordinary ACMM population
policy. Missing planned slots are not imputed. When all finite observed run-level outcomes for a
requirement are identical, the point interaction is still reported but the interval status is
`degenerate_observed_outcome`; ACMM does not present a zero-width nonparametric interval as strong
uncertainty evidence.

## Outputs

The R stage writes normalized tables under `analysis_output/` and PNG figures under:

```text
analysis_output/figures/interaction/
```

The Python presentation stage builds:

```text
analysis_output/interaction_analysis.xlsx
```

The workbook embeds the R-generated figures but the PNG files remain standalone report artifacts. The
core `analysis_report.xlsx` is unchanged.

R is the preferred source of statistical figures so population selection, factor construction, and
visualization share one analysis path. Excel-native charts remain an acceptable fallback when an R
rendering is impractical or when workbook-native interactivity is the primary goal.
