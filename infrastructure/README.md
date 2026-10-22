# ACMM Infrastructure

This directory contains version-controlled infrastructure definitions for ACMM experiment
subjects. It is an installation package: the repository ships desired-state profiles,
ready-to-use deployment defaults, and managed agent extensions, while mutable deployed
state lives outside Git.

The default mutable runtime root is:

```text
~/acmm_runtime
```

The currently managed agents are Hermes and Open WebUI:

```text
infrastructure/
|-- hermes/
|   |-- profile.yaml
|   |-- defaults/
|   `-- plugins/acmm-tools/
`-- open-webui/
    |-- profile.yaml
    |-- defaults/
    `-- tools/acmm_tools.py
```

Each agent directory describes version-controlled desired state. Mutable deployed state lives
under the matching sibling directory in `~/acmm_runtime/`. Agent Zero will use the same
source/deployment split when it is integrated.

## Infrastructure CLI architecture

`acmm-infra` exposes a common lifecycle for every managed agent: `init`, `up`, `down`,
`apply`, `apply-tools`, `status`, `doctor`, and `reset`. Full `apply` means reconcile the
qualified baseline for that agent; `apply-tools` is always a narrower Tool-only path. The
implementation may differ by product: Hermes uses a one-off provisioning container, while
Open WebUI temporarily starts its backend because supported application configuration lives
behind authenticated APIs.

The root CLI only integrates agent CLI adapters. Each adapter owns its command-specific
arguments, orchestration, and presentation while the agent manager remains focused on
infrastructure state and operations. Agent-specific commands may extend the common lifecycle
when needed.
