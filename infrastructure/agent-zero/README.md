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

```text
acmm-infra agent-zero init
acmm-infra agent-zero apply
acmm-infra agent-zero up
acmm-infra agent-zero status
acmm-infra agent-zero doctor
acmm-infra agent-zero down
acmm-infra agent-zero reset configuration --yes
```

`apply` reconciles the experiment-independent Agent Zero baseline while the ordinary service is
stopped. It uses one temporary provisioning service to repair ACMM-owned deployment files, profile
state, model preset, settings, and empty baseline Tool/plugin policy, then stops that service.

Research plugins and Tool allow-lists are supplied only through package-aware Core orchestration:

```text
acmm package apply <experiment-selector>
acmm run <experiment-selector> --apply-if-needed
```

`up` starts the persistent service and waits for the HTTP backend. `down` preserves `/a0/usr`.

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

The qualified main and utility model identities, Ollama route, and requested context are composed
from the registered ACMM LLM definition. The Agent Zero profile itself owns only the model-preset
wiring and experiment-independent embedding configuration. This keeps LLM Rebasing separate from
agent-profile identity.

Memory is disabled by the ACMM profile, so the configured embedding model remains valid background
configuration but is not an intended experimental channel.

`doctor` queries Agent Zero's model-configuration API and model-search endpoint. This
separates configuration drift from external Ollama/model availability in the same way
that the Open WebUI baseline does.

## Agent Profile and experiment isolation

The repository owns a sparse user Agent Profile under:

```text
/a0/usr/agents/acmm/
```

The profile keeps Agent Zero's ordinary reasoning/retry behavior while removing optional
capability and background-work confounders. It owns a small coherent prompt set under
`/a0/usr/agents/acmm/prompts/` for local-model compatibility. The communication and Tools-wrapper
overrides require one JSON Tool request with only `tool_name` and `tool_args`; the response Tool
override removes Agent Zero's inherited legacy `thoughts`/`headline` example; and the repeat
warning requires a meaningfully different next action. The solving override treats the initial
user/assistant pair as framework bootstrap context, preserves the most recent real user task across
Tool-result turns, explicitly identifies Tool-result history messages and `[EXTRAS]` as framework
continuation metadata, and prevents completed Tool work from being restarted.
`fw.initial_user_message.md` pins Agent Zero v2.14's required synthetic user-first seed to the inert
upstream value `Hello!`. `fw.initial_message.md` keeps the paired synthetic initial assistant
message on the same two-field Tool-call schema. Together these overrides reproduce the relevant
local-model safeguards from Agent Zero v2.14's Tiny Local prompt discipline while retaining ACMM's
experiment-specific profile and Tool policy. The prompt set is treated as one versioned compatibility
bundle rather than a collection of independently swappable snippets.

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

Agent Zero research plugins are owned by Experiment Packages. A package apparatus component may
declare managed plugin source, destination under `/a0/usr`, and Tool policy. The package-aware
manager resolves those bytes from the verified staged snapshot, applies them through the supported
Agent Zero interfaces/container-owned paths, and records exact package/apparatus provenance.

The runtime adapter may update package-owned plugin state through Agent Zero's supported plugin API
for a run and then restore the package baseline. Runtime-owned files such as activation markers or
plugin-local data are excluded from source-drift comparison where explicitly declared. Baseline
`apply` removes active research plugins and restores an empty Tool allow-list.

## Readiness boundary

At this stage, `doctor` qualifies both deployment and application baseline state:

- Docker and Docker Compose availability;
- repository Compose identity;
- managed environment values;
- exact Agent Profile/local-model prompt/model/isolation files;
- when package provenance is active, exact staged plugin files and Tool allow-list;
- running container and exact pinned image;
- observed Agent Zero release tag;
- HTTP backend responsiveness;
- persisted managed Agent Zero settings;
- effective `acmm` profile model configuration;
- availability of the configured registered LLM through the declared provider route.

A `READY` result therefore means the Agent Zero infrastructure/application state matches its
declared baseline or active package context. The experiment-facing runtime adapter is documented in
`docs/runtime/agent_zero_adapter.md`.
