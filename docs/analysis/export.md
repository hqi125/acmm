# Deterministic Analysis Export

## Purpose

`acmm export` projects one completed experiment execution into analysis-ready CSV tables.
The export is mechanical: it preserves acquisition facts and opportunity judgments without
computing ACMM estimators, scores, confidence intervals, or other statistical results.

The source execution remains canonical. CSV files are downstream interchange artifacts.

## Command

```text
acmm export ~/acmm_generated/results/<experiment_id>/execution_<execution_id>
```

By default the export is published under:

```text
~/acmm_generated/exports/<experiment_id>/execution_<execution_id>/
├── runs.csv
├── judgments.csv
└── export_manifest.yaml
```

A different root may be selected with `--output-root`. Existing export directories are never
overwritten implicitly.

## Validation gate

Export begins by validating the completed acquisition bundle:

```text
execution manifest
    -> Contract snapshot hash
    -> Contract validation
    -> manifest slot/attempt bindings
    -> Run Record validation
    -> evidence artifact existence + SHA-256
```

If the canonical bundle has drifted, has been partially edited, or contains inconsistent
manifest/record metadata, export fails before publishing CSV files.

## `runs.csv`

`runs.csv` contains one row per realized attempt, not merely one row per selected run. This
preserves invalid attempts and retry history.

Important fields include:

```text
execution_id / experiment_id / contract_sha256
run_index / repetition / attempt / run_id
selected
validity / termination
failure_kind / failure_message
subject/model/environment provenance
execution pacing and retry bounds
evidence_count / judgment_count
result_path
```

`selected=true` identifies the scientifically selected valid attempt for a resolved planned
slot. Invalid attempts remain present with `selected=false`. The exporter does not silently
remove them.

Open subject/environment configuration mappings are encoded as compact, key-sorted JSON
inside CSV cells so their structure is not flattened into an unstable set of columns.

## `judgments.csv`

`judgments.csv` contains one row per judgment that actually exists in a Run Record. The table
retains the recorded outcome exactly:

```text
compliant
violation
not_applicable
unadjudicable
```

Each row also joins deterministic Contract metadata needed for downstream grouping:

```text
opportunity_id
requirement_id
dimension
check_id
evaluator
```

Judgment evidence references are encoded as a JSON array. No denominator-eligibility flag is
added: downstream statistical code decides how opportunity/scenario/run estimators treat each
raw outcome.

Invalid attempts with no judgments therefore appear in `runs.csv` but contribute no fabricated
rows to `judgments.csv`. If an invalid attempt contains diagnostic partial judgments, those
judgments are exported with that attempt's `validity=invalid` and `selected=false` markers.

## Determinism

CSV column order and row order are fixed. Rows follow execution-manifest slot order, attempt
order, and Run Record judgment order. CSV uses a fixed UTF-8/LF representation; boolean values
are written as `true`/`false`; structured cells use canonical compact JSON.

`export_manifest.yaml` contains no generation timestamp. It records the source execution
identity/hash plus row counts and SHA-256 digests for both CSV tables. Re-exporting the same
unchanged completed execution to a different empty output root therefore produces identical
`runs.csv`, `judgments.csv`, and `export_manifest.yaml` bytes.

## Statistical boundary

The exporter intentionally does not compute:

```text
p-hat opportunity/scenario/run estimators
S_D dimension scores
weights or compensation rules
confidence intervals
power/hypothesis tests
regression models
plots
```

Those remain responsibilities of the separate R/RStudio analysis workspace.
