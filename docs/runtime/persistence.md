# Result Persistence

## Purpose

Result persistence turns an in-memory `executed_run` into a portable per-run result
directory.

Persistence is deliberately separate from execution orchestration:

```text
planned_run
    |
execute_planned_run()
    |
executed_run
    |
persist_executed_run()
    |
published result directory
```

The writer does not execute agents, evaluate opportunities, or change scientific
judgments.

## Directory layout

The generated-artifact root defaults to `~/acmm_generated` and can be overridden with `ACMM_GENERATED_ROOT`. The immediate-execution result namespace is `results/` beneath that root.

The default layout is:

```text
~/acmm_generated/results/
└── <experiment_id>/
    └── run_<run_index>_<run_id>/
        ├── run_record.yaml
        └── evidence/
            └── ...
```

For example:

```text
results/
└── runtime_execution_fixture_v1/
    └── run_000000_e3f07c5d-44bc-40c8-bf8a-3c8957489fb2/
        ├── run_record.yaml
        └── evidence/
            ├── tool_calls.json
            └── final_response.txt
```

The run index is zero-padded for convenient lexical ordering. The realized run UUID keeps
multiple attempts for the same planned slot distinct.

## Bundle consistency

Before writing anything, the persistence layer verifies that the Run Record evidence
manifest and the captured artifact bundle agree exactly.

For every evidence entry:

- the evidence ID must exist exactly once in memory;
- the evidence channel must match;
- the relative artifact path must match;
- the SHA-256 digest must match the captured bytes.

Extra or missing in-memory artifacts are rejected.

Artifact paths must be unique. Experiment IDs, run IDs, and artifact paths are also checked
defensively before they are used to construct filesystem paths.

These checks intentionally repeat a small amount of upstream validation because
persistence is a filesystem safety boundary.

## Atomic publication

A result is first written to a hidden temporary directory inside the same experiment
directory:

```text
.<final-directory-name>.tmp-...
```

All evidence artifacts and `run_record.yaml` are completed there first.

Only after successful writing does the persistence layer rename the temporary directory to
its final visible name.

Because the temporary and final directories are on the same filesystem, the rename acts as
the publication boundary: ordinary write/process failures cannot leave a partially written
directory at the final result path.

If writing fails, the temporary directory is removed.

An already-existing final result directory is never overwritten.

## Durability boundary

Evidence files are flushed and `fsync()` is requested before publication.

The current v0.1 implementation does not claim full power-loss durability for directory
metadata across every supported operating system/filesystem. Stronger crash-durability
rules, if required, can be added independently later.

The current guarantee is therefore best described as:

> atomic publication against ordinary application/process write failures, without
> overwriting an existing realized run.

## Run Record serialization

The persisted Run Record is written as:

```text
run_record.yaml
```

The serialized record is the same logical object carried by `executed_run.record`.

Evidence bytes are written exactly as captured. Their SHA-256 values in the record therefore
continue to identify the persisted evidence content.

## Current exclusions

This layer intentionally does not yet:

- snapshot the Experiment Contract into an experiment-result directory;
- maintain an experiment-level manifest;
- choose a preferred attempt when multiple realized runs occupy one planned slot;
- retry invalid executions;
- execute an entire experiment plan;
- expose `acmm run`.

Those belong to the experiment/campaign execution layer.
