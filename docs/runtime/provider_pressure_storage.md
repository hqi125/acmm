# Provider Pressure durable storage

Provider Pressure runtime history uses a bounded segmented store under each provider/model lineage.
The current store generation is `store_v2` schema 0.2. Legacy `trajectory-index.yaml` plus cumulative
trajectory snapshot stores remain immutable historical artifacts, but Core will not extend a legacy
lineage in place.

## Bounded store layout

```text
<lineage>/
├── store_v2/
│   ├── lineage.json
│   ├── profile-state.yaml
│   ├── segments/
│   │   ├── segment_000000.jsonl
│   │   ├── segment_000000.meta.json
│   │   └── ...
│   ├── checkpoints/
│   │   ├── checkpoint_000000.json
│   │   └── ...
│   ├── blobs/
│   │   └── <sha256>.json
│   └── applied_sources/
│       └── <sha256>.json
└── legacy artifacts, when present
```

`lineage.json` is a constant-sized head record. It stores terminal event/checkpoint identities and
the active segment counters; it does not contain an ever-growing event array. Provider events are
appended one record at a time to JSONL segments. A segment is sealed and hash-addressed when either
its record-count or byte bound is reached. New stores persist one of four named segment policies:
`small` (1 MiB / 256 records), `balanced` (8 MiB / 2048 records), `large` (16 MiB / 4096 records),
or `xlarge` (32 MiB / 8192 records). `balanced` is the default for both new live-runtime stores and
fresh migrations. Existing stores retain their persisted policy; stores created before policy metadata
was added are treated as the original 1 MiB / 256-record policy. Event records are individually hash
chained across segment boundaries. Checkpoints form a separate hash chain and contain only current model/profile
state plus compact source metadata, never the complete historical event collection.

Large individual payloads are externalized to content-addressed `blobs/` records. The mutable
operational profile has a hard size bound; a provider model whose current state exceeds that bound
must compact its model state instead of allowing `profile-state.yaml` to become a history store.

Live runtime active-segment writes are newline-terminated and fsynced per event. The offline legacy
upgrader uses a bounded 64-record durability batch because its immutable source can be replayed after
an interruption; it also commits at segment and snapshot/checkpoint boundaries. Startup validates the
durable event chain and truncates only an incomplete final line. A malformed durable line or hash-chain
mismatch fails closed.

## Runtime rules

Provider signals, decisions, and pacing events are persisted as they occur. Core keeps only bounded
transient collections needed by the active execution (for example recent decision lookup). Execution
finalization writes a compact checkpoint rather than constructing a cumulative trajectory snapshot.

Applied historical-source markers are stored as independent constant-sized files instead of an
unbounded list inside the profile document.

## Upgrading a legacy store

Migration is deliberately a repository maintenance tool, not an `acmm` CLI lifecycle command:

```text
python tools/upgrade_provider_pressure_store.py ~/acmm_generated/provider_pressure
```

A fresh migration defaults to the `balanced` segment policy. A different supported preset can be
selected explicitly, for example:

```text
python tools/upgrade_provider_pressure_store.py ~/acmm_generated/provider_pressure --segment-size large
```

The choice is persisted in `lineage.json`. A resumed migration must retain the existing policy; the
tool will not silently change segment sizing partway through a lineage.

The upgrader never deletes or rewrites legacy artifacts. It creates a sibling `store_v2` for each
legacy lineage. Legacy indexes and large snapshot YAML files are parsed with an event-level streaming
reader so the complete historical document is not materialized in memory. Migration commits one
legacy snapshot at a time and records migration provenance in checkpoints.

Migration is restart-safe and reports durable segment seals plus per-snapshot checkpoint completion. If the process stops after some events from a legacy snapshot are durable
but before its checkpoint is committed, rerunning the tool verifies the existing segmented event
chain, counts already-migrated records for that snapshot, and resumes without duplicating them.
Small legacy snapshots have their canonical legacy snapshot digest recomputed. Very large legacy
snapshots are streamed without canonical body reserialization; their index/metadata chain and raw
file SHA-256 are retained in the migration report instead. This distinction is explicit rather than
silently weakening verification.

After migration, normal Provider Pressure runtime and export prefer `store_v2`. The legacy exporter
keeps compatibility for modest historical stores but refuses oversized legacy YAML before calling
`yaml.safe_load`; the maintenance upgrader is the required path for those artifacts.

## Analysis/export behavior

`acmm export provider-pressure` streams segmented checkpoints/events into deterministic analysis
CSVs. Raw signals, decisions, and pacing events remain available for audit. The exporter also emits
a compact per-checkpoint/reason pacing summary so ordinary R pressure analysis does not need to load
the potentially much larger raw pacing-event table.

The general storage invariant is: no required lifecycle operation should need to materialize an
artifact whose size grows with Provider Pressure execution history. Statistical analysis may still
scale with the normalized data genuinely required by the requested estimator or plot.
