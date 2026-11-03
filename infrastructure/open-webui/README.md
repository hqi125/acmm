# Open WebUI Infrastructure Baseline

This directory is the version-controlled ACMM deployment and application baseline for
Open WebUI.

The qualified deployment target is:

```text
Open WebUI:       0.11.4
Docker image:     ghcr.io/open-webui/open-webui:v0.11.4
Image digest:     sha256:9591b13f13843c7721c2b8eaf7382846c81b3ffe126526d1888d1fed50c6a33f
Ollama route:     http://host.docker.internal:11434
LLM:              composed from registered ACMM definition
Web UI auth:      disabled for the isolated ACMM instance
OpenAI API:       disabled
Plugins:          enabled for ACMM-managed workspace Tools
Runtime pip:      disabled for tool/function frontmatter requirements
Telemetry:        disabled
Experiment extras: disabled (web search, memory, code/image execution, background tasks)
```

The browser frontend remains part of the upstream image, but it is not part of the ACMM
experiment execution path. ACMM uses Open WebUI's backend interfaces directly.

The Open WebUI startup secret is persisted through the mounted data directory. ACMM sets
`WEBUI_SECRET_KEY_FILE=/app/backend/data/.webui_secret_key`; Open WebUI creates the key on
first startup and reuses it after container recreation. The key value itself is runtime state
and is never stored in the repository.

## Source layout

```text
open-webui/
|-- profile.yaml
|-- defaults/
|   |-- compose.yaml
|   `-- env.defaults
`-- README.md
```

The subject directory contains only experiment-independent baseline state. Research Tools are owned
by Experiment Packages and are consumed from their immutable staged apparatus roots.

Initialization creates mutable deployment state under `~/acmm_runtime/open-webui/`.

## Lifecycle

```text
acmm-infra open-webui init
acmm-infra open-webui apply
acmm-infra open-webui up
acmm-infra open-webui status
acmm-infra open-webui doctor
acmm-infra open-webui down
acmm-infra open-webui reset configuration --yes
```

`apply` is the stopped-service baseline reconciler. It updates deployment state, temporarily starts
the backend, waits for `/ready`, reconciles provider and isolation settings through supported admin
APIs, removes active research Tools, and returns the ordinary service to stopped baseline state.
ACMM never edits `webui.db` directly.

Research Tool state enters through package orchestration:

```text
acmm package apply <experiment-selector>
acmm run <experiment-selector> --apply-if-needed
```

The package-aware manager imports, repairs, and removes only the Tools declared by the staged
apparatus component. Per-run Valve/configuration values remain runtime fixture state rather than
static infrastructure state. Runtime installation of package requirements from Tool frontmatter is
disabled so provisioning remains deterministic.

## Readiness

`up` waits for Open WebUI's `/ready` endpoint before reporting success. `status` reports deployment
and live application observations; `doctor` verifies the pinned image/version, provider and isolation
settings, model availability, and—when package provenance is active—the exact staged Tool apparatus
identity.

A baseline workflow is `init -> apply -> up -> doctor`. Package-aware application is performed by
Core before execution, and `doctor` passively reconstructs the active package context from deployment
provenance rather than accepting an experiment selector.

## Persistent state and reset

`down` is deliberately non-destructive. The bind-mounted `data/` directory survives ordinary
container shutdown/removal and retains the secret key, database, and other application state.
Experiment Tools are explicitly reconciled or removed by the apparatus/baseline commands.

The current `configuration` reset removes `data/webui.db` while the container is stopped. It
is intended only for rebuilding the isolated Open WebUI application database from a fresh
state. The persistent secret key is not removed by this scope.

Experiment-run isolation is a separate concern from infrastructure reset. Conversation/run
cleanup and execution-adapter behavior will be added without turning ordinary `down` into a
factory reset.
