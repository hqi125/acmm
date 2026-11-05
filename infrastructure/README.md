# ACMM Infrastructure

This directory contains experiment-independent deployment defaults for the qualified subjects and
registered LLM baselines. Subject-specific Python implementation lives under
`src/acmm_infra/subjects/<subject>/`.

Current subjects:

- Hermes Agent;
- Open WebUI; and
- Agent Zero.

Research experiments do not live below these subject directories. Experiment Packages own their
Contracts, runtime code, and optional generic integration components. Core validates and stages the
package, then constructs package-aware subject managers from the immutable snapshot.

## Subject source layout

Each subject owns a narrow module tree:

```text
src/acmm_infra/subjects/<subject>/
    api.py or client.py
    manager.py
    profile.py
    cli.py
    payloads/
```

`payloads/` contains executable/configuration payload sources that must be sent into the subject
container. Keeping those payloads out of manager/client source makes reconciliation code readable and
lets payload changes be reviewed independently.

Cross-subject cohort/factory logic remains outside individual subject directories.

## LLM composition

Model selection is a baseline concern:

```text
acmm env set-llm <llm-id>
acmm env status
acmm env doctor --start-if-needed
acmm env stop
```

`set-llm` reconciles the qualified cohort to the chosen registered LLM and returns subjects to
experiment-independent capability state.

## Internal subject CLI

For integration/debugging work:

```text
acmm-infra <subject> init
acmm-infra <subject> apply
acmm-infra <subject> up
acmm-infra <subject> status
acmm-infra <subject> doctor
acmm-infra <subject> down
```

These commands do not select research experiments. Package integration is orchestrated through
`acmm package apply` or permissioned `acmm run --apply-if-needed`.

Deployment provenance is stored in the subject runtime root and records the active package/integration
revision when one is attached.
