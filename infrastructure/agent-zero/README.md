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
acmm-infra agent-zero apply-tools
acmm-infra agent-zero up
acmm-infra agent-zero status
acmm-infra agent-zero doctor
acmm-infra agent-zero down
acmm-infra agent-zero reset configuration --yes
```

`apply` is the full baseline reconciler. It requires the ordinary Agent Zero service to
be stopped. It first reconciles the ACMM-owned Compose/environment files, then starts one
temporary Agent Zero service and keeps it up for the entire application-provisioning
phase. Managed plugin/profile files are written through that running container so host
UID/GID differences cannot block writes under `/a0/usr`. The model preset is reconciled
through Agent Zero's own Model Configuration API, application settings through the
settings API, and the effective model/profile state is verified before the same
provisioning service is stopped. It does not restart the container between individual
settings.

`apply-tools` is the narrower Tool/plugin path. It uses one temporary Agent Zero container to replace the
repository-managed ACMM plugin while Agent Zero is stopped; it does not reapply model,
profile, or application settings.

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
capability and background-work confounders. It also owns three narrow prompt overrides under
`/a0/usr/agents/acmm/prompts/` for local-model compatibility. The communication override
requires one JSON Tool request with only `tool_name` and `tool_args`; the solving override
prevents completed Tool work from being restarted merely because older requests remain visible in
conversation history; and `fw.initial_message.md` keeps Agent Zero's synthetic initial assistant
message on the same two-field Tool-call schema so conversation history does not demonstrate the
legacy `thoughts`/`headline` envelope. These overrides follow Agent Zero v2.14's Tiny Local prompt
discipline without adopting the rest of the Tiny Local profile.

In particular:

- Tool policy defaults to block and allows only
  `plugin:acmm_tools:get_magic_number` and `plugin:acmm_tools:add_numbers`;
- MCP Tool policy defaults to block;
- Skills visibility defaults to block with no allowed Skills;
- memory recall, memorization, and consolidation are disabled;
- automatic chat naming is disabled;
- the profile explicitly selects the managed `Default` model preset.

The goal is not to turn Agent Zero into a different agent. Core response handling,
reasoning, retry, and repair behavior remain available; ACMM removes auxiliary
capabilities that would add uncontrolled experiment channels or background model calls.

## Managed ACMM tools

The repository-managed custom plugin is:

```text
infrastructure/agent-zero/plugins/acmm_tools/
```

It is deployed to:

```text
/a0/usr/plugins/acmm_tools/
```

The plugin contains controlled `get_magic_number` and `add_numbers` Tool implementations
plus agent-facing prompt fragments. `default_config.yaml` defines the controlled baseline
state. The Tools read configuration through Agent Zero's normal plugin-config resolver; the
runtime adapter writes per-run values through the supported plugin API and restores the
baseline afterward. ACMM does not maintain a private Tool-state file.

## Readiness boundary

At this stage, `doctor` qualifies both deployment and application baseline state:

- Docker and Docker Compose availability;
- repository Compose identity;
- managed environment values;
- exact repository-managed ACMM plugin files;
- exact Agent Profile/local-model prompt/model/isolation files;
- running container and exact pinned image;
- observed Agent Zero release tag;
- HTTP backend responsiveness;
- persisted managed Agent Zero settings;
- effective `acmm` profile model configuration;
- availability of `nemotron-3-nano:30b-cloud` through the configured Ollama route.

A `READY` result therefore means the Agent Zero infrastructure/application baseline is
qualified. The experiment-facing runtime adapter is documented in
`docs/runtime/agent_zero_adapter.md`; frozen Experiment 1 remains unbound until the per-run
step-budget difference is resolved or explicitly qualified.
