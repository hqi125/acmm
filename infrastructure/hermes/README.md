# Hermes Agent Infrastructure

Hermes Agent is pinned by the qualified profile under `infrastructure/hermes/profile.yaml`; its Python
implementation lives under `src/acmm_infra/subjects/hermes/`.

The baseline is intentionally experiment-independent. It configures the registered LLM, context
window, reasoning effort, terminal backend, Blank Slate restrictions, memory policy, and essential
skills. Research capabilities are added only through Experiment Package integrations.

## Reconciliation

`apply` requires the persistent Hermes runtime to be stopped and performs one bounded provisioning
pass. Package-aware construction may add generic managed configuration through an integration, but
Hermes-specific mechanics stay inside the Hermes subject implementation or package integration—not in
Core's scientific model.

When a package is detached or baseline state is reapplied, integration-managed Hermes configuration
keys that are no longer desired are removed.

## Payloads

Executable programs sent into the Hermes container live in:

```text
src/acmm_infra/subjects/hermes/payloads/
```

This includes Blank Slate reconciliation, provider/model observation, skill synchronization, and
small inspection scripts. Manager/client modules load those payload sources instead of embedding
Python or shell programs inline.

## Lifecycle

```text
acmm-infra hermes init
acmm-infra hermes apply
acmm-infra hermes up
acmm-infra hermes status
acmm-infra hermes doctor
acmm-infra hermes down
```

Research package application uses `acmm package apply <selector>`.
