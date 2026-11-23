# ACMM Documentation

This directory contains operator guidance and reference documentation for ACMM Core. The root [`README.md`](../README.md) is the recommended first entry point; it covers first-time setup, daily workflow, and the two execution entry points without reproducing subsystem details.

## Getting started

- [`../README.md`](../README.md) — environment, first-time setup, daily workflow, `acmm run`, `acmm campaign`, export, and analysis.
- [`security/secrets.md`](security/secrets.md) — local secret binding and public-release safeguards.
- [`experiments/package.md`](experiments/package.md) — Experiment Package lifecycle and Core/package ownership boundary.
- [`../infrastructure/README.md`](../infrastructure/README.md) — qualified subject infrastructure and environment commands.

## Runtime and execution

- [`runtime/campaign.md`](runtime/campaign.md) — durable Campaign creation, execution, resume, adjudication, and merge.
- [`runtime/experiment_execution.md`](runtime/experiment_execution.md) — immediate multi-run execution semantics.
- [`runtime/execution.md`](runtime/execution.md) — single-run execution orchestration and validity boundaries.
- [`runtime/planning.md`](runtime/planning.md) — deterministic run planning and selection.
- [`runtime/persistence.md`](runtime/persistence.md) — result layout, atomic publication, and durability.
- [`runtime/materialization.md`](runtime/materialization.md) — Campaign materialization and package runtime hooks.
- [`runtime/provider_usage.md`](runtime/provider_usage.md) — optional provider usage telemetry.
- [`runtime/provider_pressure_storage.md`](runtime/provider_pressure_storage.md) — bounded Provider Pressure store and legacy migration.
- [`runtime/cli.md`](runtime/cli.md) — researcher-facing command families and ownership boundaries.

Subject adapter reference:

- [`runtime/hermes_adapter.md`](runtime/hermes_adapter.md)
- [`runtime/open_webui_adapter.md`](runtime/open_webui_adapter.md)
- [`runtime/agent_zero_adapter.md`](runtime/agent_zero_adapter.md)

## Export and analysis

- [`analysis/export.md`](analysis/export.md) — deterministic export scopes and source provenance.
- [`analysis/plan.md`](analysis/plan.md) — first-class versioned analysis plans.
- [`analysis/availability.md`](analysis/availability.md) — availability measurement and retry interpretation.
- [`analysis/interaction.md`](analysis/interaction.md) — generic interaction estimands and outputs.
- [`analysis/boundary.md`](analysis/boundary.md) — acquisition/analysis separation.
- [`../analysis/r/README.md`](../analysis/r/README.md) — R implementation, dependencies, estimators, and direct script entry points.

## Data model reference

- [`contract/semantics.md`](contract/semantics.md) — Experiment Contract v0.1 semantics.
- [`contract/v0_2_semantics.md`](contract/v0_2_semantics.md) — Contract v0.2 representation-fidelity additions.
- [`record/semantics.md`](record/semantics.md) — Run Record v0.1 semantics.
- [`../contracts/v0_1/README.md`](../contracts/v0_1/README.md) and [`../contracts/v0_2/README.md`](../contracts/v0_2/README.md) — schema locations.
- [`../records/v0_1/README.md`](../records/v0_1/README.md) — Run Record schema location.

## Infrastructure and integration

- [`../infrastructure/README.md`](../infrastructure/README.md) — subject infrastructure overview.
- [`infrastructure/llm_rebasing.md`](infrastructure/llm_rebasing.md) — changing the shared LLM baseline without changing the research treatment.
- [`infrastructure/llm_rebasing_workflow.md`](infrastructure/llm_rebasing_workflow.md) — concise operator sequence for LLM qualification.
- [`infrastructure/hermes.md`](infrastructure/hermes.md) — Hermes-specific infrastructure notes.
- [`integration/agent_integration.md`](integration/agent_integration.md) — requirements for adding or qualifying a subject integration.

## Engineering utilities

[`../tools/README.md`](../tools/README.md) documents repository utilities that support analysis, infrastructure qualification, or maintenance. They are not research Experiment Packages unless explicitly stated.

## Documentation scope

Public documentation focuses on reproducible operation, externally visible semantics, and stable artifact formats. Historical implementation notes, one-off qualification diaries, and internal project checklists are intentionally not part of the public documentation set. Frozen scientific artifacts retain their own provenance independently of this documentation.
