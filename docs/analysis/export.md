# Deterministic Analysis Export

## Purpose

`acmm export` provides separate mechanical export scopes for immediate executions and durable
campaigns. Both preserve acquisition facts and opportunity judgments without computing ACMM
estimators, scores, confidence intervals, or other statistical results.

Canonical acquisition/merge artifacts remain authoritative. CSV files are downstream interchange
artifacts.

## Commands

Execution export (schema 0.1):

```text
acmm export run ~/acmm_generated/results/<experiment_id>/execution_<execution_id>
```

Campaign export (schema 0.3):

```text
acmm export campaign \
    ~/acmm_generated/<experiment_id>/campaign_<campaign_id> \
    ~/acmm_generated/<experiment_id>/campaign_<campaign_id>/merged/merge_<merge_id>
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

## Campaign export

Campaign export deliberately requires both the Campaign root and one immutable merge directory. The
Campaign contributes the frozen planned population plus acquisition history across Campaign
executions, including retries and non-selected attempts. The merge contributes the authoritative
accepted observation for each logical run and, when adjudication was selected, the final revised
judgments. The exporter never mutates either source and never uses analysis findings to repair or
rerun acquisition.

By default schema 0.3 is published under:

```text
~/acmm_generated/exports/<experiment_id>/campaign_<campaign_id>/merge_<merge_id>/
├── planned_runs.csv
├── runs.csv
├── judgments.csv
├── tool_calls.csv
├── tool_results.csv
└── export_manifest.yaml
```

`planned_runs.csv` contains exactly one row per frozen Campaign logical slot, including slots with no
realized attempt in a partial campaign. `runs.csv` contains realized attempts across Campaign
executions. `source_selected` preserves the source-execution selection while `selected` identifies
the observation accepted by the chosen merge. Selected observations use the merged Run Record as the
authoritative judgment source; non-selected attempts retain their recorded source judgments.

Schema 0.3 additionally normalizes the acquisition-time `tool_calls` and `tool_results` evidence
artifacts for every persisted Campaign attempt, including non-selected/invalid retry history. The two
tables preserve stable run/attempt identity, tool names, transport names when available, call/result
IDs, canonical call input JSON, result text/error markers, and recorded timestamps. These are
descriptive trajectory facts; export does not infer recovery, verification, mutation, redundancy, or
other semantic labels. Campaign export schema 0.2 remains readable by the R loader for previously
published exports, but trajectory analysis requires a fresh schema-0.3 export.

The exporter verifies that the merge belongs to the supplied Campaign and binds the Campaign ID,
experiment ID, design digest, package/Contract provenance, Campaign-manifest hash, population, source
execution artifacts, source Run Record hashes, merged Run Record hashes, and optional adjudication
provenance. A mismatch is fatal rather than reconciled heuristically.

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
identity/hash plus row counts and SHA-256 digests for every published CSV table. Re-exporting the same
unchanged completed execution to a different empty output root therefore produces identical
`runs.csv`, `judgments.csv`, and `export_manifest.yaml` bytes for schema 0.1; Campaign schema 0.3 applies the same deterministic publication rule to all five CSV tables.

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
