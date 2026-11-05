# Hermes Infrastructure Notes

The canonical operational documentation is `infrastructure/hermes/README.md`. This note records the
Core implementation boundary for Hermes.

Hermes-specific code lives under `src/acmm_infra/subjects/hermes/` and is divided into profile,
client, manager, CLI, and executable payload sources. Baseline reconciliation uses Hermes's supported
configuration/skill interfaces and remains independent of any research experiment.

Experiment Package integrations may compose generic managed Hermes configuration before the normal
manager applies desired state. Concrete capability semantics remain in the package integration.
Returning to baseline removes integration-managed keys that are no longer declared.

The Hermes runtime adapter is documented in `docs/runtime/hermes_adapter.md`.
