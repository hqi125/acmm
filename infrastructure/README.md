# ACMM Infrastructure

This directory contains experiment-independent deployment profiles for the qualified ACMM subjects and registered LLM baselines. Research experiments do not live here; Experiment Packages own their Contracts, runtime code, integrations, assets, and study-specific state.

Current qualified subjects are:

- Hermes Agent;
- Open WebUI; and
- Agent Zero.

Pinned versions and images are declared in the subject profiles under `infrastructure/<subject>/`.

## Researcher-facing environment commands

Normal operation uses the cohort-level environment commands:

```text
acmm env set-llm <llm-id>
acmm env status
acmm env doctor --start-if-needed
acmm env stop
```

`set-llm` reconciles the qualified cohort to one registered LLM baseline and returns subjects to experiment-independent capability state. Research capability integrations are applied separately through Experiment Packages.

Registered LLM definitions are under `infrastructure/llm-baselines/`. See [`../docs/infrastructure/llm_rebasing.md`](../docs/infrastructure/llm_rebasing.md) before changing the shared baseline.

## Subject-specific diagnostics

The lower-level `acmm-infra` command family is intended for infrastructure diagnosis, subject development, and integration work:

```text
acmm-infra <subject> init
acmm-infra <subject> apply
acmm-infra <subject> up
acmm-infra <subject> status
acmm-infra <subject> doctor
acmm-infra <subject> down
```

These commands do not select research experiments or attach Experiment Package capabilities.

Subject-specific notes:

- [`hermes/README.md`](hermes/README.md)
- [`open-webui/README.md`](open-webui/README.md)
- [`agent-zero/README.md`](agent-zero/README.md)

## Source ownership

Subject implementation lives under `src/acmm_infra/subjects/<subject>/`. Managers own host reconciliation; runtime adapters own normalized execution interaction/evidence; Experiment Package integrations request study-specific capability state through the generic integration boundary.
