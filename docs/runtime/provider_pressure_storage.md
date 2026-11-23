# Provider Pressure Durable Storage

Provider Pressure runtime history uses a bounded segmented store under each provider/model lineage. The current generation is `store_v2`. Legacy cumulative YAML trajectories remain immutable historical artifacts but are not extended in place.

## Store layout

```text
<lineage>/
└── store_v2/
    ├── lineage.json
    ├── profile-state.yaml
    ├── segments/
    │   ├── segment_000000.jsonl
    │   ├── segment_000000.meta.json
    │   └── ...
    ├── checkpoints/
    │   ├── checkpoint_000000.json
    │   └── ...
    ├── blobs/
    │   └── <sha256>.json
    └── applied_sources/
        └── <sha256>.json
```

`lineage.json` is a bounded head record. Provider events are appended to JSONL segments and hash-chained across segment boundaries. Checkpoints form a separate hash chain and contain current model/profile state plus compact source metadata rather than complete event history.

Large individual payloads can be externalized to content-addressed blobs. The mutable operational profile has its own size bound so current state cannot silently become another history store.

## Segment policies

New stores persist one named segment policy:

| Preset | Maximum bytes | Maximum records |
| --- | ---: | ---: |
| `small` | 1 MiB | 256 |
| `balanced` | 8 MiB | 2,048 |
| `large` | 16 MiB | 4,096 |
| `xlarge` | 32 MiB | 8,192 |

`balanced` is the default for new live stores and fresh migrations. Existing stores retain the policy they were created with.

Live runtime writes remain durable per event. The offline legacy upgrader can batch durability because its immutable source is replayable after interruption.

## Runtime behavior

Signals, decisions, and pacing events are persisted as they occur. Core keeps only bounded transient state required by the active execution. Finalization writes a compact checkpoint instead of constructing a cumulative trajectory snapshot.

Startup validates the durable chain and may truncate only an incomplete final line. A malformed durable line or hash-chain mismatch fails closed.

## Upgrading a legacy store

Migration is a repository maintenance tool, not part of the normal `acmm` lifecycle:

```text
python tools/upgrade_provider_pressure_store.py <provider-pressure-root>
```

`<provider-pressure-root>` is the generated directory containing legacy Provider Pressure lineages.

A fresh migration uses the `balanced` policy. A different supported preset can be selected explicitly:

```text
python tools/upgrade_provider_pressure_store.py <provider-pressure-root> --segment-size large
```

The upgrader:

- never deletes or rewrites legacy artifacts;
- creates `store_v2` beside each legacy lineage;
- parses large legacy YAML with an event-level streaming reader;
- commits durable checkpoints as migration progresses;
- resumes without duplicating already migrated events after interruption; and
- records migration provenance and reconciliation information.

A resumed migration preserves the existing store's segment policy rather than changing it midway through a lineage.

## Export and analysis

```text
acmm export provider-pressure <provider-pressure-root>
```

The exporter streams native segmented stores and produces deterministic raw/audit tables plus compact pacing, admission, and utilization summaries. Ordinary Provider Pressure analysis consumes the compact summaries and therefore does not need to load the largest raw CSV tables.

The storage invariant is simple: no required lifecycle operation should need to materialize an artifact whose size grows with the entire Provider Pressure history. Downstream statistical work may still scale with the normalized data genuinely required by a requested analysis.
