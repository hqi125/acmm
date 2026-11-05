# Hermes Runtime Adapter

The Hermes adapter executes one ACMM run through the pinned Hermes session interface and returns
normalized evidence. It does not install experiment tools or select package capabilities.

The qualified Hermes baseline is managed by `src/acmm_infra/subjects/hermes/`. Experiment-specific
capabilities are reconciled before execution through the selected Experiment Package integration.

For each run the adapter supplies the scenario prompt and supported termination controls, captures
conversation/tool activity exposed by Hermes, and records the final response. Native tool identities
are preserved in evidence; ACMM does not rename them to historical synthetic-tool names.

A package may cause Hermes to expose additional native tools through its integration, but the adapter
remains unchanged.
