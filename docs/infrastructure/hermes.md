# Hermes Infrastructure Management

## Responsibility

`acmm-infra` manages reproducible Hermes deployment/configuration state. It is separate
from `acmm`, which owns Experiment Contracts, execution, evidence, Run Records, and later
analysis export.

The repository is the installation package:

```text
~/acmm_repo/
└── infrastructure/
    └── hermes/
        ├── profile.yaml
        ├── defaults/
        │   ├── compose.yaml
        │   └── env.defaults
        └── plugins/
            └── acmm-tools/
```

The initialized deployment is mutable and WSL-native:

```text
~/acmm_runtime/
└── hermes/
    ├── compose.yaml
    ├── .env
    └── data/
        ├── .env
        └── plugins/
```

## Qualified ACMM baseline

The current desired state reproduces the Hermes setup already qualified for ACMM:

```text
Hermes version         0.21.3
container image        nousresearch/hermes-agent:v2026.9.14
model.provider         custom
model.base_url         http://host.docker.internal:11434/v1
model.api_mode         chat_completions
model.context_length   64000
model.default          nemotron-3-nano:30b-cloud
agent.reasoning_effort medium
terminal.backend         local
Blank Slate              enabled
```

Hermes talks to the Windows Ollama service through `host.docker.internal`. Windows Ollama
owns the Cloud authentication and exposes the OpenAI-compatible endpoint; no Ollama Cloud
credential is copied into the Hermes deployment.

`profile.yaml` summarizes the ACMM-relevant desired state. It is not a dump of every
Hermes setting.

## Initialization

```text
acmm-infra hermes init
```

Initialization:

```text
creates ~/acmm_runtime/hermes/
creates data/ and data/plugins/
creates an empty private data/.env if absent
copies defaults/compose.yaml -> compose.yaml if absent
copies defaults/env.defaults -> .env if absent
preserves existing deployment files
validates the deployed Compose configuration
```

It deliberately does not install experiment toolsets.

## Applying Hermes configuration

```text
acmm-infra hermes apply
```

The interactive Hermes setup wizard is not required for the qualified baseline.

`apply` uses Hermes's non-interactive configuration/plugin CLI through the image's absolute privilege-dropping launcher `/opt/hermes/bin/hermes` to set:

```text
model.provider
model.base_url
model.api_mode
model.context_length
model.default
agent.reasoning_effort
terminal.backend
auxiliary.title_generation.enabled
auxiliary.background_review.enabled
tools.tool_search.enabled
managed plugin enablement
```

It also synchronizes repository-managed plugins into the Hermes data volume.

`apply` requires the persistent gateway to be stopped. ACMM first synchronizes managed
plugin files locally, then launches exactly one named `docker compose run --rm --no-deps`
provisioning container against the same deployment/data volume. Inside that container, a
single shell batch executes all Hermes `config set` operations followed by managed-plugin
enablement.

Hermes does not currently expose a native multi-key `config set`, so batching is performed
at the container/session boundary: Hermes still validates every setting through its
supported CLI, while Docker startup happens only once.

The batch prints numbered progress lines directly to the terminal. The entire provisioning
session is bounded by `--timeout-seconds` (120 seconds by default). The shell uses fail-fast
semantics, so the first failing Hermes command terminates the batch. The one-off container
uses a unique known name; timeout, ordinary failure, and keyboard interruption trigger a
forced cleanup attempt before `acmm-infra` exits.

Inspection follows the same batching rule. `status` reads Hermes version, model-route
settings, config paths, and managed-plugin state through one read-only `docker exec` shell
session instead of one Docker command per field.



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


## Partial/legacy configuration recovery

A failed provisioning attempt may leave `data/config.yaml` behind. Before launching a new
provisioning container, `apply` now inspects that file.

If `_config_version` is present but below Hermes's supported migration floor, `apply`
refuses to continue. An absent version is allowed when the YAML is otherwise parseable so
an interrupted fresh ACMM provisioning run can be retried idempotently.

For the fresh ACMM deployment, remove only the generated configuration with:

```text
acmm-infra hermes reset configuration --yes
```

This scope removes `data/config.yaml` only. It preserves `data/.env`, plugins, sessions,
and other runtime state.

The next provisioning container will seed a current Hermes config before applying the ACMM
baseline.



## Blank Slate policy

The infrastructure profile reproduces the behavior of the pinned Hermes v0.21.3 Blank
Slate setup rather than treating "Blank Slate" as a descriptive label. ACMM delegates the
minimal-toolset and minimized-agent mutations to Hermes's own pinned
`_blank_slate_minimal_toolsets()` and `_blank_slate_minimize_config()` helpers, then removes
only explicitly declared experiment overlay toolsets such as `acmm` from the resulting hard
disabled list.

The desired minimal tool surface is:

```text
platform_toolsets.cli = [file, skills, terminal, vision]
```

Hermes also needs the second hard-suppression layer used by its own setup implementation:
`agent.disabled_toolsets` is computed from the running pinned image's configurable,
plugin-provided, and plain toolset registries. ACMM then removes only explicitly declared
experiment overlays from that disabled set. For the current pilot:

```text
managed overlay toolset = acmm
```

`acmm` is therefore available when the subject adapter explicitly requests it, but it is
not part of the default Hermes CLI surface.

The remaining pinned Blank Slate settings are:

```text
agent.max_turns                     90
compression.enabled                 false
memory.memory_enabled               false
memory.user_profile_enabled         false
checkpoints.enabled                 false
smart_model_routing.enabled         false
display.tool_progress               all
```

The Docker image synchronizes the full bundled skill catalog during its container-init
stage before the ACMM batch runs. To reproduce the wizard's essential-only result,
provisioning opts out and safely removes pristine bundled skills, then performs one
essential-only skill synchronization. The `.no-bundled-skills` marker remains present for
subsequent launches.

`doctor` verifies all of these facts from the running Hermes instance, including a dynamic
recomputation of the expected disabled-toolset list and the bundled-skill manifest.

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

Experiment-facing plugins are stored under
`infrastructure/hermes/experiments/<experiment-id>/` rather than in the qualified baseline.
Apply one explicitly before acquisition:

```text
acmm-infra hermes down
acmm-infra hermes apply --experiment controlled_tool_use_multiscenario_v1
acmm-infra hermes up
acmm-infra hermes doctor
```

The preferred `apply --experiment <experiment-id>` composes the stable baseline with the
selected experiment apparatus, atomically synchronizes the managed plugin bytes, and
reconciles the resulting settings/plugin/tool-routing state in one bounded provisioning batch.
`apply-tools --experiment <experiment-id>` performs only the narrower Tool-facing
reconciliation.

Baseline `apply` returns Hermes to the experiment-independent state and removes active
repository-managed experiment apparatus. `apply-experiment <experiment-id>` remains a
compatibility alias for the full experiment-aware apply.

`doctor` discovers the active experiment from the deployment marker and is the pre-run
qualification gate for the composed
baseline plus selected apparatus.


## Plugin consent

The ACMM pilot plugin does not need to replace Hermes built-in tools. The desired profile
therefore declares:

```text
allow_tool_override: false
```

`apply` passes Hermes's explicit negative enable flag rather than relying on the default
interactive question. This makes provisioning deterministic and records the denial in
Hermes `config.yaml`.

## Provisioning output

The one-container provisioning batch always captures the complete merged container output
for diagnostics, but display is selectable:

```text
acmm-infra hermes apply
    filtered default: ACMM stages + [Hermes n/N] markers + useful ✓ confirmations

acmm-infra hermes apply --quiet
    final success or error only

acmm-infra hermes apply --verbose
    raw Docker, S6, container, and Hermes output
```

The default mode filters output while the process is running, so progress remains visible
without flooding the console. On failure, ACMM reports a concise error-focused subset of
the captured raw output. Timeout and Ctrl+C cleanup still force-remove the named temporary
provisioning container.


## Lifecycle

The preferred fresh-install sequence is:

```text
acmm-infra hermes init
acmm-infra hermes apply
acmm-infra hermes up
acmm-infra hermes status
acmm-infra hermes doctor
```

`status` observes and reports the qualified route plus the Blank Slate toolset, memory, turn-limit, and bundled-skill state.

`doctor` requires the running deployment to match those desired values, the complete Blank Slate baseline, the expected image/version, deployed Compose baseline, and managed plugin state.

## Reset

Reset is explicit, profile-scoped, and requires `--yes`:

```text
acmm-infra hermes reset sessions --yes
acmm-infra hermes reset memory --yes
acmm-infra hermes reset experiment --yes
```

The manager refuses reset while Hermes is running.

## Existing-state adoption

An older qualified Hermes data directory can still be copied into the new deployment:

```text
acmm-infra hermes adopt-data /path/to/old/data --yes
```

This is now a migration/fallback path rather than the main installation path. Fresh
initialization plus deterministic `apply` is the preferred reproducible baseline.


## Offline doctor behavior

`doctor` separates checks that can be evaluated from deployment files from checks that require
a running Hermes process. If Hermes is stopped, Docker/Compose/deployment/plugin-sync checks
still run, then readiness ends with a clear failed `container_running` check. Live model, Blank
Slate, plugin-enable, and plugin-policy checks are not fabricated from missing observations.

If Hermes is running but the single batched live inspection fails, `doctor` reports one
`live_observation` failure and stops the dependent live checks.

## Readiness

The pre-experiment gate remains:

```text
acmm-infra hermes doctor
        |
        +-- READY
        v
acmm live execution
```
