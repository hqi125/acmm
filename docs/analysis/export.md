# Deterministic Analysis Export

`acmm export` converts validated ACMM artifacts into deterministic downstream interchange tables. Export is mechanical: it preserves acquisition, adjudication, merge, and telemetry facts without computing ACMM estimators, confidence intervals, fitted models, or interpretive labels.

Canonical acquisition and merge artifacts remain authoritative.

## Path placeholders

- `<execution-directory>` — one completed immediate execution directory.
- `<campaign-directory>` — one generated `campaign_<id>` directory.
- `<merge-directory>` — one immutable `merge_<id>` directory for that Campaign.
- `<provider-pressure-root>` — generated Provider Pressure root containing one or more lineages.
- `<export-root>` — optional destination root passed with `--output-root`.

## Commands

Immediate execution export:

```text
acmm export run <execution-directory>
```

Campaign export:

```text
acmm export campaign <campaign-directory> <merge-directory>
```

Provider Pressure export:

```text
acmm export provider-pressure <provider-pressure-root>
```

Use `--output-root <export-root>` when the deterministic export should be published outside the default generated-artifact tree.

Existing export directories are never silently overwritten.

## Run export

Run export schema 0.1 validates the completed execution bundle and writes:

```text
runs.csv
judgments.csv
export_manifest.yaml
```

`runs.csv` contains one row per realized attempt, including invalid retry history. `selected=true` identifies the scientifically selected valid attempt for a resolved planned slot.

`judgments.csv` contains only judgments actually present in Run Records and preserves their recorded outcomes. Invalid attempts without judgments do not generate fabricated judgment rows.

## Campaign export

Campaign export schema 0.3 requires **both** the frozen Campaign and one immutable merge. The two inputs represent different facts:

- the Campaign contributes the complete planned population and acquisition history across Campaign executions, including retries and non-selected attempts;
- the merge contributes the authoritative accepted observation for each logical run and, when applicable, adjudicated judgments.

The exporter never mutates either source and never uses analysis findings to repair acquisition.

Schema 0.3 writes:

```text
planned_runs.csv
runs.csv
judgments.csv
tool_calls.csv
tool_results.csv
export_manifest.yaml
```

`planned_runs.csv` contains one row per frozen logical slot, including an unattempted slot in an explicitly partial Campaign. `runs.csv` preserves realized attempts. Selected observations use the merged Run Record as the authoritative judgment source; non-selected attempts retain their original acquisition records.

The tool-call/result tables normalize recorded trajectory evidence without inferring recovery, verification, mutation, redundancy, or other semantic behavior labels.

Campaign export verifies Campaign/merge identity, design digest, package/Contract provenance, source and merged Run Record hashes, population, and optional adjudication provenance. A mismatch is fatal.

## Provider Pressure export

Provider Pressure is a separate export family. The current schema 0.6 writes:

```text
lineages.csv
snapshots.csv
features.csv
signals.csv
decisions.csv
pacing_events.csv
pacing_summary.csv
admission_summary.csv
utilization_summary.csv
export_manifest.yaml
```

For native `store_v2` input, the exporter streams segmented records and verifies event/checkpoint hash chains while keeping source ingestion bounded. The compact summary tables are produced during that streaming pass so ordinary R analysis does not need to load the much larger raw decision or pacing-event tables.

`utilization_summary.csv` records run-level mean/maximum utilization for the provider dimensions actually recorded by the controller. Utilization is expressed relative to the **recorded operational threshold**; export does not reinterpret that threshold as a provider hard rate limit.

Raw signal, decision, and pacing tables remain available for audit and follow-up analysis.

### Legacy Provider Pressure stores

Oversized legacy YAML trajectories should be migrated offline rather than loaded as one YAML object:

```text
python tools/upgrade_provider_pressure_store.py <provider-pressure-root>
```

The maintenance tool writes `store_v2` beside the immutable legacy files, never deletes the source, and resumes from durable checkpoints after interruption. See [`../runtime/provider_pressure_storage.md`](../runtime/provider_pressure_storage.md).

## Validation gate

Before publication, export validates the source identity and relevant integrity relationships. Depending on the source family, this includes Contract hashes, manifest bindings, Run Record validation, evidence artifact hashes, merge bindings, adjudication provenance, and Provider Pressure hash chains.

If canonical artifacts have drifted or are internally inconsistent, export fails rather than reconciling them heuristically.

## Determinism

Column order and row order are fixed for each export schema. Structured cells use canonical JSON representations. `export_manifest.yaml` records source identity, row counts, and SHA-256 values for published tables without adding a generation timestamp that would make unchanged exports differ.

Re-exporting the same unchanged source to a different empty destination therefore produces the same deterministic export bytes for that schema.

## Statistical boundary

Export intentionally does not compute compliance estimators, GOBPFR dimension scores, compensation rules, uncertainty intervals, fitted models, or report interpretations. Those responsibilities belong to the downstream analysis layer selected through `acmm analyze`.

See [`plan.md`](plan.md) and [`../../analysis/r/README.md`](../../analysis/r/README.md).
