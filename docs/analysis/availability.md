# Availability Analysis

ACMM treats acquisition availability as a measurement property that is distinct from compliance.
A planned experimental run can fail to yield a scientifically usable observation even when the
experiment definition itself is valid. Timeouts, adapter failures, malformed subject interactions,
service failures, and other execution problems can all prevent a selected planned slot from producing one
selected valid Run Record.

The availability layer formalizes that imperfection without converting missing observations into
compliance outcomes.

## Measurement model

The fundamental unit is the selected planned run slot from the Experiment Contract plan.

For selected planned slot `i`, define:

```text
A_i = 1  if the declared run protocol produces one selected valid attempt
A_i = 0  otherwise
```

For any analysis group `G`:

```text
availability(G) = sum(A_i) / |G|
```

This denominator is the planned design, not the observed valid population. A scientifically invalid
attempt is therefore neither compliant nor violating; it contributes to acquisition unavailability.

Availability is evaluated after the Contract's declared retry protocol. The analysis also reports
first-attempt availability and retry recovery separately so retries do not disappear inside the final
slot-level fraction.

## Three distinct layers

ACMM keeps three concepts separate:

1. **planned opportunity** -- the Contract intended a subject/scenario/repetition slot to be observed;
2. **acquisition availability** -- the run protocol produced a selected valid Run Record for that
   slot; and
3. **adjudication coverage** -- within an available valid run, an applicable compliance opportunity
   was actually adjudicable rather than `unadjudicable`.

The existing compliance coverage statistic belongs to layer 3. Availability belongs to layer 2.
Neither statistic substitutes for the other.

## Operational failure fields are not causal labels

For unavailable slots, ACMM reports the final recorded `termination`, `failure_kind`, and
`failure_message`. These are runtime observations, not automatic causal attribution.

For example, a `harness_error` can be triggered by malformed subject tool use, an adapter/service
failure, or another condition that crosses the harness boundary. The availability analysis therefore
does not rename every harness error as an infrastructure failure. Causal diagnosis should use the
Run Record, evidence, and diagnostics and should be documented separately when needed.

## Cell and ordering diagnostics

Availability is reported overall, by subject, by `subject_id + scenario_id`, and by within-repetition
position. The scenario table detects condition-dependent missingness. The position table helps detect
ordering/startup effects, especially for experiments using cyclic scenario rotation.

A pooled compliance estimate can be difficult to interpret when availability differs strongly by
subject or scenario. The availability workbook should therefore be reviewed before treating an
`available-valid` compliance population as a neutral subsample of the planned experiment.

## Availability uncertainty

Subject-level availability receives a scenario-to-slot hierarchical percentile bootstrap interval:

1. resample observed scenarios with replacement;
2. within each sampled scenario, resample its selected planned slots with replacement;
3. recompute the subject's availability fraction; and
4. form percentile bounds from the finite bootstrap replicates.

The interval describes observed acquisition reliability across the experiment's scenario/run
structure. It is not a model of the compliance outcomes that would have been observed in unavailable
runs. When every observed slot for a subject has the same availability outcome, ACMM reports the point
fraction but marks the bootstrap interval `degenerate_observed_availability` instead of presenting a
zero-width interval as strong uncertainty evidence.

## Compliance sensitivity envelope

For each `subject_id + requirement_id`, the availability analysis also reports a deterministic
opportunity-weighted sensitivity envelope. Let:

```text
C = observed compliant opportunities
V = observed violation opportunities
M = planned requirement opportunities belonging to unavailable run slots
```

Then:

```text
observed p_hat = C / (C + V)

all-missing-violation lower = C / (C + V + M)
all-missing-compliant upper = (C + M) / (C + V + M)
```

The envelope does not assume missing-at-random and is not a confidence interval. It asks how far the
observed opportunity-weighted compliance estimate could move under two explicit extreme imputations.

`M` is inferred from the observed scenario opportunity template. ACMM requires that the observed
number of opportunities for the scenario/requirement combination be consistent across available
runs. If a required scenario template was never observed, or the observed template is inconsistent,
the sensitivity bounds are left unreported rather than guessed.

The first implementation deliberately limits this missing-outcome envelope to the opportunity-weighted
requirement estimand. Run- and scenario-weighted missing-outcome sensitivity can be added later if a
clear general imputation contract is justified.

## Retry interpretation

A final available slot can require multiple attempts. The availability analysis reports:

```text
first_attempt_availability_fraction
retried_slots
recovered_by_retry_slots
retry_recovery_fraction
mean_attempts_per_slot
```

This preserves the difference between an agent/runtime that succeeds immediately and one that becomes
usable only because the protocol permits retries. A future experiment should declare retry policy
before acquisition; selectively rerunning only failed canonical slots after seeing results changes the
measurement protocol and should be treated as supplemental diagnosis rather than silent replacement.

## Outputs

`tools/analyze_execution.py` runs availability analysis for every experiment. It writes a separate
supplementary workbook so the core `analysis_report.xlsx` remains focused:

```text
analysis_output/logs/availability/availability_analysis.txt
analysis_output/data/availability/availability_summary.csv
analysis_output/data/availability/availability_cells.csv
analysis_output/data/availability/availability_positions.csv
analysis_output/data/availability/availability_slots.csv
analysis_output/data/availability/availability_failure_modes.csv
analysis_output/data/availability/availability_attempt_outcomes.csv
analysis_output/data/availability/availability_intervals.csv
analysis_output/data/availability/availability_sensitivity.csv
analysis_output/availability_analysis.xlsx
```

The workbook contains Overview, Summary, Cells, Positions, Slot Outcomes, Failure Modes, Attempt
Outcomes, Intervals, and Sensitivity sheets. The CSV tables remain the normalized machine-readable
analysis artifacts.

For an execution with missing selected planned slots, use:

```text
python tools/analyze_execution.py \
    <execution-directory>
```

The ordinary compliance analyses remain conditional on the observed valid population. The separate
availability analysis describes the acquisition loss and quantifies missing-outcome sensitivity; it
does not rewrite invalid slots as compliance outcomes.
