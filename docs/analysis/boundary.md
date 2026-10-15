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
