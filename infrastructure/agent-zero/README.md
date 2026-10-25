# ACMM Agent Zero Infrastructure

This directory defines the repository-managed Agent Zero subject baseline for ACMM.
Mutable runtime state is deployed outside the repository under:

```text
~/acmm_runtime/agent-zero/
```

The pinned subject is Agent Zero `v2.14` using the immutable image reference in
`profile.yaml`. Only Agent Zero's user-state root is bind-mounted:

```text
~/acmm_runtime/agent-zero/usr -> /a0/usr
```

Application/framework code therefore stays owned by the pinned image while settings,
profiles, chats, plugins, and other Agent Zero user state persist across ordinary
`down` / `up` cycles.

## Lifecycle

Agent Zero implements the common ACMM infrastructure vocabulary:

```text
acmm-infra agent-zero init
acmm-infra agent-zero apply
acmm-infra agent-zero apply --experiment controlled_tool_use_multiscenario_v1
acmm-infra agent-zero up
acmm-infra agent-zero status
acmm-infra agent-zero doctor
acmm-infra agent-zero down
acmm-infra agent-zero reset configuration --yes
```

`apply` is the full baseline reconciler. It requires the ordinary Agent Zero service to
be stopped. It first reconciles the ACMM-owned Compose/environment files, then starts one
temporary Agent Zero service and keeps it up for the entire application-provisioning
phase. Baseline profile files are written through that running container so host UID/GID
differences cannot block writes under `/a0/usr`. The model preset is reconciled through
Agent Zero's own Model Configuration API, application settings through the settings API,
and the effective model/profile state is verified before the same provisioning service is
stopped. The baseline blocks all experiment Tools and removes active repository-managed
experiment plugins.

`apply --experiment <experiment-id>` is the preferred full experiment path: it composes the
baseline with the selected apparatus, then uses one temporary provisioning-service lifetime
to reconcile the resulting profile, experiment-owned plugin bytes, Tool allow-list, model,
and persistent settings before recording the apparatus SHA-256. `apply-tools --experiment <experiment-id>`
performs only the Tool/plugin phase. `apply-experiment <experiment-id>` remains a compatibility
alias for the full experiment-aware apply.

`up` starts the persistent service and waits until the HTTP backend responds before
returning. `down` stops/removes the Compose service while preserving `usr/`.

## Agent Zero API session and CSRF

Agent Zero v2.14 protects its ordinary WebUI/plugin API handlers with CSRF validation,
even when UI login itself is disabled. ACMM therefore uses the same supported request
flow as the WebUI: obtain a token from `GET /api/csrf_token`, preserve the Flask session
cookie, and send the token in `X-CSRF-Token` on protected API requests. A 403 causes one
token refresh and retry so a newly started Agent Zero process can establish a fresh
session cleanly.

The API client does not bypass or disable Agent Zero's CSRF protection.

## Persistent settings

Agent Zero can bootstrap core settings from `A0_SET_*` environment variables. If
`usr/settings.json` does not yet exist, those values are used in memory and Agent Zero
does not need to create the file merely to start.

ACMM uses those environment values as safe first-boot defaults, but the qualified
baseline does not stop there. Full `apply` sends the managed settings through Agent
Zero's supported settings API. Agent Zero then owns serialization of the resulting
`usr/settings.json` file. This keeps ACMM out of the application's internal settings
format while still making persisted state reproducible.

The managed core settings select the `acmm` Agent Profile and disable experiment
confounders including MCP/A2A servers, update checks, project inheritance, and automatic
RFC Docker behavior.

## Model configuration in v2.14

Agent Zero v2.14 resolves durable model choices through the always-enabled
`_model_config` plugin rather than treating the old `A0_SET_chat_model_*` family as the
qualified model contract.

ACMM manages the global `Default` preset at:

```text
/a0/usr/plugins/_model_config/presets.yaml
```

and pins the `acmm` Agent Profile to that preset at:

```text
/a0/usr/agents/acmm/plugins/_model_config/config.json
```

The qualified model baseline is:

```text
Main provider:       ollama
Main base URL:       http://host.docker.internal:11434
Main model:          nemotron-3-nano:30b-cloud
Main context:        64000

Utility provider:    ollama
Utility base URL:    http://host.docker.internal:11434
Utility model:       nemotron-3-nano:30b-cloud
Utility context:     64000

Embedding provider:  huggingface
Embedding model:     sentence-transformers/all-MiniLM-L6-v2
```

Memory is disabled by the ACMM profile, so the embedding model remains part of a valid
Agent Zero model configuration but should not participate in Experiment 1 execution.

`doctor` queries Agent Zero's model-configuration API and model-search endpoint. This
separates configuration drift from external Ollama/model availability in the same way
that the Open WebUI baseline does.

## Agent Profile and experiment isolation

The repository owns a sparse user Agent Profile under:

```text
/a0/usr/agents/acmm/
```

The profile keeps Agent Zero's ordinary reasoning/retry behavior while removing optional
capability and background-work confounders. It also owns four narrow prompt overrides under
`/a0/usr/agents/acmm/prompts/` for local-model compatibility. The communication override
requires one JSON Tool request with only `tool_name` and `tool_args`; the solving override
prevents completed Tool work from being restarted merely because older requests remain visible in
conversation history; `fw.initial_user_message.md` preserves Agent Zero's required synthetic
user-first bootstrap turn while marking it explicitly as a non-task initialization placeholder; and
`fw.initial_message.md` keeps the paired synthetic initial assistant message on the same two-field
Tool-call schema so conversation history does not demonstrate the legacy `thoughts`/`headline`
envelope. These overrides follow Agent Zero v2.14's Tiny Local prompt discipline without adopting
the rest of the Tiny Local profile.

In particular:

- baseline Tool policy defaults to block and allows no experiment Tools;
- experiment-aware `apply` replaces that empty allow-list with the selected experiment's exact
  apparatus Tool policy;
- MCP Tool policy defaults to block;
- Skills visibility defaults to block with no allowed Skills;
- memory recall, memorization, and consolidation are disabled;
- automatic chat naming is disabled;
- the profile explicitly selects the managed `Default` model preset.

The goal is not to turn Agent Zero into a different agent. Core response handling,
reasoning, retry, and repair behavior remain available; ACMM removes auxiliary
capabilities that would add uncontrolled experiment channels or background model calls.

## Experiment apparatus

Agent-facing custom plugins are owned per experiment, for example:

```text
infrastructure/agent-zero/experiments/controlled_tool_use_multiscenario_v1/
|-- apparatus.yaml
`-- plugins/acmm_tools/
```

The selected plugin is deployed to its apparatus-defined destination under `/a0/usr` (for
Experiment 1, `/a0/usr/plugins/acmm_tools/`). Experiment 1's plugin contains the controlled
`get_magic_number` and `add_numbers` Tools plus agent-facing prompt fragments. Its
`default_config.yaml` defines controlled baseline state. The runtime adapter writes per-run
values through Agent Zero's supported plugin API and restores the experiment baseline
afterward. Agent Zero may persist runtime-owned plugin state such as `config.json`, activation
markers, or plugin-local `data/`; readiness compares repository-owned plugin source exactly
while excluding those runtime-owned files from source-drift checks.

A later experiment may duplicate and change this plugin without mutating Experiment 1's
frozen apparatus. Full baseline `apply` removes the active experiment plugin and restores an
empty Tool allow-list.

## Readiness boundary

At this stage, `doctor` qualifies both deployment and application baseline state:

- Docker and Docker Compose availability;
- repository Compose identity;
- managed environment values;
- exact Agent Profile/local-model prompt/model/isolation files;
- when an experiment marker is active, exact plugin files and Tool allow-list for the
  discovered experiment;
- running container and exact pinned image;
- observed Agent Zero release tag;
- HTTP backend responsiveness;
- persisted managed Agent Zero settings;
- effective `acmm` profile model configuration;
- availability of `nemotron-3-nano:30b-cloud` through the configured Ollama route.

A `READY` result therefore means the Agent Zero infrastructure/application state matches its
declared baseline or active experiment context. The experiment-facing runtime adapter is documented in
`docs/runtime/agent_zero_adapter.md`.
