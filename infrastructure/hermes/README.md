# Hermes Infrastructure Baseline

This directory is the version-controlled ACMM installation definition for Hermes Agent.

The qualified baseline is:

```text
Hermes Agent:    0.21.3
Docker image:    nousresearch/hermes-agent:v2026.9.14
Provider:        custom
Base URL:        http://host.docker.internal:11434/v1
API mode:        chat_completions
Model:           composed from registered LLM
Context:         composed from registered LLM
Reasoning:       medium
Terminal:        local
Experiment apparatus: none in baseline
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
└── README.md
```

`profile.yaml` defines the experiment-independent Hermes baseline. Research plugins are supplied by
installed Experiment Packages and resolved from their staged apparatus roots; Core no longer stores
per-experiment plugin trees below `infrastructure/hermes/`.

Initialization creates the mutable WSL-native deployment under `~/acmm_runtime/hermes/`. Existing
deployed `compose.yaml` and `.env` files are preserved rather than overwritten.

## Lifecycle

Fresh baseline:

```text
acmm-infra hermes init
acmm-infra hermes apply
acmm-infra hermes up
acmm-infra hermes status
acmm-infra hermes doctor
```

`apply` requires the persistent gateway to be stopped. It reconciles the qualified subject baseline
in one bounded one-off Compose container and leaves Hermes free of research apparatus. The LLM fields
are supplied by the registered LLM definition selected through Core rather than by an experiment
lookup in this directory.

For research execution, install/apply the Experiment Package through the top-level CLI:

```text
acmm package apply <experiment-selector>
acmm run <experiment-selector>
```

or allow a selected run to reconcile the required subject with `--apply-if-needed`. Package
apparatus is synchronized from the immutable staged package snapshot and its digest is recorded in
deployment provenance.

Reset remains scoped and explicit. `acmm-infra hermes reset configuration --yes` is available for
recovery from an interrupted fresh provisioning attempt; ordinary `down` is non-destructive.

## Blank Slate baseline

The ACMM baseline profile reproduces Hermes v0.21.3's pinned Blank Slate policy without
installing an experiment plugin. A selected experiment may add an explicit plugin/toolset
overlay after the baseline has been reconciled.

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
four minimal toolsets, while `agent.disabled_toolsets` hard-suppresses the remaining toolsets.
Package-aware orchestration may add explicitly declared apparatus toolsets after the baseline is
reconciled. The runtime adapter must still request the package-declared toolset for a run. A later
baseline `apply` removes that apparatus again.

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

## ACMM experiment isolation

Blank Slate is an official Hermes setup policy. ACMM adds a separate experiment-isolation
policy for features that Blank Slate does not disable but that can introduce hidden model
calls or routing indirection:

```text
auxiliary.title_generation.enabled   false
auxiliary.background_review.enabled  false
tools.tool_search.enabled            off
```

This separation is deliberate. These settings are ACMM methodological controls, not claims
about Hermes's definition of Blank Slate.

Title generation is disabled because Hermes can make an auxiliary model request to generate
a session title. Background review is disabled because it can create a separate review run.
Tool Search is disabled so the explicitly requested `acmm` toolset is presented directly to
the subject instead of being deferred behind Hermes's `tool_call` routing bridge.

`status` reports these values and `doctor` requires them to match before an experiment is
READY.

## Experiment apparatus reconciliation

Hermes research apparatus is owned by an installed Experiment Package, not by this directory.
`acmm package apply <selector>` performs full-package preparation, while
`acmm run <selector> --apply-if-needed` may reconcile only Hermes when selected runs require it.

The package-aware manager receives an already verified staged apparatus root, synchronizes its
managed plugin content, composes the package tool policy over the qualified baseline, and records
exact package/apparatus provenance. `doctor` reconstructs that same package-aware manager from the
deployment marker and verifies staged identity; it never falls back to a main-repository experiment
path.

Running baseline `acmm-infra hermes apply` or `acmm env set-llm <llm-id>` returns Hermes to the
experiment-independent baseline.

## Plugin consent policy

`acmm-tools` is enabled with explicit denial of built-in tool replacement:

```text
plugins.entries.acmm-tools.allow_tool_override = false
```

Package-aware apparatus application passes Hermes's explicit negative CLI flag, so apparatus provisioning
never waits for the interactive built-in-tool-override consent prompt.

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
## Offline doctor behavior

`doctor` separates checks that can be evaluated from deployment files from checks that require
a running Hermes process. If Hermes is stopped, Docker/Compose/deployment/plugin-sync checks
still run, then readiness ends with a clear failed `container_running` check. Live model, Blank
Slate, plugin-enable, and plugin-policy checks are not fabricated from missing observations.

If Hermes is running but the single batched live inspection fails, `doctor` reports one
`live_observation` failure and stops the dependent live checks.

