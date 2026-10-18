# Hermes Infrastructure Baseline

This directory is the version-controlled ACMM installation definition for Hermes Agent.

The qualified baseline is:

```text
Hermes Agent:    0.21.3
Docker image:    nousresearch/hermes-agent:v2026.9.14
Provider:        custom
Base URL:        http://host.docker.internal:11434/v1
API mode:        chat_completions
Model:           nemotron-3-nano:30b-cloud
Context:         64000
Reasoning:       medium
Terminal:        local
Plugin:          acmm-tools enabled
```

The custom endpoint is the Windows Ollama service. Ollama owns Cloud authentication;
Hermes does not store an Ollama Cloud API key for this ACMM baseline.

## Source layout

```text
hermes/
├── profile.yaml
├── defaults/
│   ├── compose.yaml
│   └── env.defaults
├── plugins/
│   └── acmm-tools/
└── README.md
```

`profile.yaml` is the authoritative non-secret summary of ACMM-relevant desired state.
It is used by `apply`, `status`, and `doctor`.

`defaults/compose.yaml` and `defaults/env.defaults` are ready-to-use installation files.
They are copied into the mutable deployment by:

```text
acmm-infra hermes init
```

Initialization creates:

```text
~/acmm_runtime/hermes/
├── compose.yaml
├── .env
└── data/
    ├── .env
    └── plugins/
```

The deployment `.env` controls Docker Compose. `data/.env` is the Hermes-side environment
file. v0.1 initializes the Hermes file empty with private permissions; this qualified
Windows-Ollama route needs no Hermes-side provider secret.

Existing deployed `compose.yaml` and `.env` files are preserved rather than overwritten.

## Lifecycle

Fresh baseline:

```text
acmm-infra hermes init
acmm-infra hermes apply
acmm-infra hermes up
acmm-infra hermes status
acmm-infra hermes doctor
```

`apply` requires the persistent gateway to be stopped. It runs one named one-off Compose
container sharing the same `data/` volume and executes the complete Hermes reconciliation
as a single shell batch. The official image routes an executable command such as `sh`
through its normal entrypoint/privilege-drop path, so ACMM does not bypass Hermes's Docker
bootstrap.

The batch has a default 120-second timeout, configurable with:

```text
acmm-infra hermes apply --timeout-seconds 180
```

Every Hermes sub-operation prints a numbered progress line. A failed sub-command aborts the
batch immediately. Timeout, failure, and Ctrl+C trigger cleanup of the named provisioning
container.

`apply` currently reconciles:

```text
model.provider         = custom
model.base_url         = http://host.docker.internal:11434/v1
model.api_mode         = chat_completions
model.context_length   = 64000
model.default          = nemotron-3-nano:30b-cloud
agent.reasoning_effort = medium
terminal.backend         = local
```

It also synchronizes `acmm-tools` into `data/plugins/acmm-tools` and reconciles managed
plugin enablement.

Experiment toolset selection is still not part of `init`. The experiment adapter chooses
the toolset for a run.

Reset remains scoped and explicit:

```text
acmm-infra hermes reset sessions --yes
acmm-infra hermes reset memory --yes
acmm-infra hermes reset experiment --yes
```

Adoption of an older Hermes data directory remains available, but it is a migration path
rather than the preferred fresh-install workflow.

If an earlier failed provisioning attempt left an unversioned or pre-v12 `config.yaml`,
reset only that generated configuration before retrying:

```text
acmm-infra hermes reset configuration --yes
```

The manager uses `/opt/hermes/bin/hermes` inside Docker batches instead of relying on the
container shell's `PATH`.



## Blank Slate baseline

The ACMM profile reproduces Hermes v0.21.3's pinned Blank Slate policy, then adds only the
ACMM experiment plugin as an explicit overlay.

The base CLI surface is:

```text
file
skills
terminal
vision
```

The profile also requires:

```text
agent.max_turns                     90
compression.enabled                 false
memory.memory_enabled               false
memory.user_profile_enabled         false
checkpoints.enabled                 false
smart_model_routing.enabled         false
display.tool_progress               all
bundled skills                      essential_only
```

Hermes applies tool restrictions at two levels. `platform_toolsets.cli` contains only the
four minimal toolsets, while `agent.disabled_toolsets` hard-suppresses the remaining
toolsets. ACMM-managed plugin toolsets such as `acmm` are deliberately excluded from that
hard-disabled set, but they are not added to the default CLI surface. The experiment
adapter must still request `--toolsets acmm` explicitly for a run.

Docker seeds bundled skills during container initialization before the ACMM provisioning
script begins. `apply` therefore adapts Hermes's Blank Slate flow by running:

```text
hermes skills opt-out --remove --yes
```

and then re-synchronizing the essential bundled skills. This leaves the opt-out marker in
place so future container starts do not re-seed the full catalog.

`doctor` verifies the minimal toolset list, dynamically expected hard-disabled toolsets,
Blank Slate scalar settings, bundled-skills opt-out marker, and that the bundled manifest
contains only Hermes's essential skills.

## Plugin consent policy

`acmm-tools` is enabled with explicit denial of built-in tool replacement:

```text
plugins.entries.acmm-tools.allow_tool_override = false
```

`acmm-infra hermes apply` passes Hermes's explicit negative CLI flag, so provisioning never
waits for the interactive built-in-tool-override consent prompt.

## Apply output modes

The default `apply` output suppresses Docker/S6 bootstrap and shutdown chatter while
streaming ACMM stage progress, `[Hermes n/N]` markers, and useful Hermes `✓ ...` confirmations:

```text
acmm-infra hermes apply
```

Minimal output:

```text
acmm-infra hermes apply --quiet
```

Raw Docker/container/Hermes output for diagnostics:

```text
acmm-infra hermes apply --verbose
```

Timeout and temporary-container cleanup behavior is identical in all three modes.


## Provisioning user and retry semantics

The Hermes Docker image's main wrapper activates the bundled virtual environment and drops
the one-off `sh -lc ...` provisioning process to the `hermes` user before ACMM's batch
starts. ACMM must therefore not invoke `s6-setuidgid hermes` again inside that shell.

Custom Python used for the pinned Blank Slate helpers runs directly through:

```text
/opt/hermes/.venv/bin/python
```

inside the already-unprivileged provisioning shell.

`apply` is also intentionally retryable. A failed fresh provisioning attempt may leave a
parseable `config.yaml` without `_config_version`, because the supported `hermes config set`
commands can create partial configuration before the later Blank Slate step runs. ACMM
accepts that unversioned partial state and reapplies the deterministic baseline. A present
but malformed version, or a version below the supported migration floor, is still rejected.

