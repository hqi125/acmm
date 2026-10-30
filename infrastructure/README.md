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
|-- llm-baselines/
|   |-- catalog.yaml
|   `-- <baseline-id>.yaml
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

- `llm-baselines/` owns registered model-specific desired state and identifies the current LLM
  baseline;
- `profile.yaml` and other subject-root files define experiment-independent agent/framework state;
- `experiments/<experiment-id>/` owns the exact agent-facing apparatus for that experiment;
- scenario/run state remains controlled by the runtime fixture and adapter rather than by
  creating a different deployed package per scenario.

Small apparatus duplication across experiments is deliberate. A frozen experiment keeps its
own deployable bytes even when a later experiment starts from similar tools.


## LLM baseline composition

The model baseline is deliberately separate from the agent profile. A subject is provisioned by
composing the agent profile with a registered LLM baseline and, when selected, experiment
apparatus. This prevents a future model switch from silently changing a frozen experiment.

The current baseline ID is declared in `infrastructure/llm-baselines/catalog.yaml`. Historical
subject bindings record their own baseline ID, so experiment-aware provisioning follows the
Contract's subject bindings rather than blindly using whichever model is currently preferred.

Context is expressed as a minimum capability plus optional per-agent requested values. The current
minimum remains 64,000 tokens, but exact equality is not treated as an eternal model identity.
See `docs/infrastructure/llm_rebasing.md` for the rebasing architecture and context policy, and
`docs/infrastructure/llm_rebasing_workflow.md` for the operational candidate-to-promotion workflow.

## Infrastructure CLI architecture

The public CLI boundary is intentionally split by operator intent:

```text
acmm env ...
    Experiment-facing environment orchestration across qualified Contract subjects.

acmm-infra <subject> ...
    Subject/backend-facing provisioning, lifecycle control, diagnosis, and maintenance.
```

The experiment-facing environment commands are:

```text
acmm env apply --experiment <experiment-id>
    Resolve the experiment's canonical Contract and reconcile every covered subject.

acmm env apply
    Reconcile experiment-independent baselines for all qualified subjects. This fallback emits a
    warning and removes active experiment apparatus.

acmm env status
    Passively discover all deployed agent environments, their active LLM baselines, experiment
    context, and convergence. This command accepts no cohort selector.

acmm env doctor
    Run the existing doctor for all three deployed agent environments and report aggregate
    readiness. This command accepts no cohort selector.
```

Repeatable `--subject <subject-id>` filters remain available for state-changing/operator commands
such as `apply` and `stop`; they do not redefine what `status` or `doctor` observes.
Environment apply verifies that every selected runtime is stopped before mutating the first
subject. It is fail-fast but not transactional: if a later subject fails after earlier subjects
were applied, the error reports the partial state and no automatic rollback is attempted.

`acmm-infra` now has one strict root shape: the first positional command is always `hermes`,
`open-webui`, or `agent-zero`. Those subject commands expose the common lifecycle `init`, `up`,
`down`, `apply`, `apply-tools`, `status`, `doctor`, and `reset`.
`apply-experiment <experiment-id>` remains a compatibility alias for subject-level
`apply --experiment <experiment-id>` during the CLI transition.

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

`acmm env` owns only thin experiment-environment orchestration and command routing.
Product-specific deployment mechanics remain in the subject managers; environment operations
resolve canonical Contract subjects, delegate to those managers, and aggregate context/readiness
results. `acmm-infra` does not expose a competing all-subject command layer.
