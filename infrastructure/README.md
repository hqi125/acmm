# ACMM Infrastructure

This directory contains version-controlled infrastructure definitions for ACMM experiment
subjects. The repository separates each subject's qualified baseline from experiment-facing
apparatus. Mutable deployed state lives outside Git.

The default mutable runtime root is:

```text
~/acmm_runtime
```

The managed source layout is:

```text
infrastructure/
|-- hermes/
|   |-- profile.yaml
|   |-- defaults/
|   `-- experiments/<experiment-id>/
|       |-- apparatus.yaml
|       `-- plugins/...
|-- open-webui/
|   |-- profile.yaml
|   |-- defaults/
|   `-- experiments/<experiment-id>/
|       |-- apparatus.yaml
|       `-- tools/...
`-- agent-zero/
    |-- profile.yaml
    |-- defaults/
    |-- profile-prompts/
    `-- experiments/<experiment-id>/
        |-- apparatus.yaml
        `-- plugins/...
```

The split is intentional:

- `profile.yaml` and other subject-root files define experiment-independent qualified state;
- `experiments/<experiment-id>/` owns the exact agent-facing apparatus for that experiment;
- scenario/run state remains controlled by the runtime fixture and adapter rather than by
  creating a different deployed package per scenario.

Small apparatus duplication across experiments is deliberate. A frozen experiment keeps its
own deployable bytes even when a later experiment starts from similar tools.

## Infrastructure CLI architecture

`acmm-infra` exposes the common lifecycle `init`, `up`, `down`, `apply`, `apply-tools`,
`status`, `doctor`, and `reset`. `apply-experiment <experiment-id>` remains a compatibility
alias for `apply --experiment <experiment-id>` during the CLI transition.

The two reconciliation layers are:

```text
acmm-infra <subject> apply
    Reconcile only the qualified subject baseline and remove active experiment apparatus.
    This baseline fallback emits a warning because no experiment was selected.

acmm-infra <subject> apply --experiment <experiment-id>
    Compose the qualified baseline with that experiment apparatus and reconcile the resulting
    effective state in one full apply.

acmm-infra <subject> apply-tools
    Reconcile baseline tool policy only and remove active experiment tool apparatus.
    This baseline fallback also emits a warning.

acmm-infra <subject> apply-tools --experiment <experiment-id>
    Reconcile only the selected experiment's tool-facing apparatus.
```

`status` and `doctor` are passive with respect to experiment selection. They read the
deployment apparatus marker, automatically compose the declared experiment state when one is
active, and otherwise describe/qualify the experiment-independent baseline. The commands do not
accept an experiment selector; an active marker is the source of truth for deployed context.

When experiment apparatus is applied, the deployment records its experiment ID, content
SHA-256, application scope (`experiment` or `tools`), and managed resource identities in
`.acmm-experiment-apparatus.json`. Baseline `apply` and baseline `apply-tools` clear this marker
after removing their corresponding experiment state. Cleanup also discovers apparatus known in
the repository so deployments created before this marker existed can be migrated safely.

The product-specific mechanics still differ: Hermes uses a one-off provisioning container,
Open WebUI uses its supported application APIs, and Agent Zero uses its supported APIs plus
container-owned writes under `/a0/usr`.

The root CLI only integrates agent CLI adapters. Each adapter owns command-specific arguments,
orchestration, and presentation while the agent manager remains focused on infrastructure
state and operations.
