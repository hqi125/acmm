# ACMM Repository Tools

The `tools/` directory contains maintenance, qualification, and compatibility utilities that are useful around the ACMM workflow but are not themselves research Experiment Packages.

## Preferred analysis entry point

Use the first-class analysis command for normal downstream work:

```text
acmm analyze <export-directory> --plan <analysis-plan>
```

`analyze_execution.py` remains as an execution-only compatibility/convenience wrapper for the established run-export workflow.

## Provider Pressure migration

`upgrade_provider_pressure_store.py` converts legacy cumulative Provider Pressure trajectories into the bounded segmented `store_v2` format. It preserves legacy source files and resumes from durable checkpoints after interruption.

```text
python tools/upgrade_provider_pressure_store.py <provider-pressure-root>
```

See [`../docs/runtime/provider_pressure_storage.md`](../docs/runtime/provider_pressure_storage.md).

## Infrastructure qualification utilities

- `cold_start_qualification.py` — measures subject runtime bring-up behavior under controlled startup boundaries.
- `qualify_llm_behavior.py` — runs the experiment-independent LLM behavioral smoke against the already deployed cohort.
- `llm_behavioral_smoke.py` — shared implementation used by the qualifier.
- subject-specific smoke utilities — focused host/adapter engineering checks.

Qualification outputs are engineering evidence and are not added to ACMM scientific populations.
