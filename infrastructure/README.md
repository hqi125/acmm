# ACMM Infrastructure

This directory contains version-controlled desired state for qualified ACMM subjects and registered
LLM baselines. Mutable deployment state lives outside Git, normally under:

```text
~/acmm_runtime
```

Core infrastructure is intentionally experiment-independent:

```text
infrastructure/
|-- llm-baselines/
|   |-- catalog.yaml
|   `-- <llm-id>.yaml
|-- hermes/
|   |-- profile.yaml
|   |-- defaults/
|   `-- README.md
|-- open-webui/
|   |-- profile.yaml
|   |-- defaults/
|   `-- README.md
`-- agent-zero/
    |-- profile.yaml
    |-- defaults/
    |-- profile-prompts/
    `-- README.md
```

Research apparatus does not live below these subject directories. Experiment Packages own their
Contracts, runtime code, and optional per-subject apparatus. ACMM validates and stages those packages
under the managed runtime store, then subject managers consume already-resolved staged apparatus.

## LLM composition

The model definition is separate from each agent profile. A qualified subject is produced by
composing its agent profile with a registered LLM definition. `infrastructure/llm-baselines/catalog.yaml`
identifies the current LLM while historical package Contracts can bind explicit registered LLM IDs.

The user-facing selection command is:

```text
acmm env set-llm <llm-id>
```

This reconciles the qualified subject cohort to that LLM and removes active research apparatus. See
`docs/infrastructure/llm_rebasing.md` for qualification, context-window policy, and promotion.

## CLI responsibility boundary

Research experiment lifecycle belongs to the Experiment Package layer:

```text
acmm package validate <package-directory>
acmm package install <package-directory>
acmm package apply <selector>
acmm run <selector>
acmm package detach <selector>
acmm package uninstall <selector>
```

Subject environment lifecycle remains separate:

```text
acmm env set-llm <llm-id>
acmm env status
acmm env doctor --start-if-needed
acmm env stop
```

The detailed internal subject CLI is baseline/engineering only:

```text
acmm-infra <subject> init
acmm-infra <subject> apply
acmm-infra <subject> apply-tools
acmm-infra <subject> up
acmm-infra <subject> status
acmm-infra <subject> doctor
acmm-infra <subject> down
acmm-infra <subject> reset ...
```

The internal subject CLI has no research-experiment selector. A subject manager receives research apparatus only from
the package-aware Core orchestration path together with exact package provenance. `status` and
`doctor` may observe active package context, but they do not select one.

Deployment provenance is persisted in the subject runtime root. Package-backed deployment markers
include the exact package, Contract/runtime, and apparatus identities needed to reconstruct the
selected manager. A marker that describes an old non-package experiment deployment is unsupported;
reconcile the subject with `acmm env set-llm <llm-id>` before proceeding.

Product mechanics still differ: Hermes uses a one-off provisioning container, Open WebUI uses its
supported application APIs, and Agent Zero uses supported APIs plus container-owned writes under
`/a0/usr`. Those differences stay inside the subject managers.
