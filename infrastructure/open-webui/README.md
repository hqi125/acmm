# Open WebUI Infrastructure Baseline

This directory is the version-controlled ACMM deployment and application baseline for
Open WebUI.

The qualified deployment target is:

```text
Open WebUI:       0.11.4
Docker image:     ghcr.io/open-webui/open-webui:v0.11.4
Image digest:     sha256:9591b13f13843c7721c2b8eaf7382846c81b3ffe126526d1888d1fed50c6a33f
Ollama route:     http://host.docker.internal:11434
Experiment model: nemotron-3-nano:30b-cloud
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
|-- experiments/
|   `-- <experiment-id>/
|       |-- apparatus.yaml
|       `-- tools/
`-- README.md
```

Initialization creates a mutable WSL-native deployment under:

```text
~/acmm_runtime/open-webui/
|-- compose.yaml
|-- .env
`-- data/
```

`compose.yaml` is ACMM-owned deployed state. `apply` replaces it with the current repository
baseline while the service is stopped. `.env` is partially managed: ACMM-owned keys are
reconciled while unrelated local keys are preserved.

## Lifecycle

Open WebUI implements the common ACMM infrastructure lifecycle:

```text
acmm-infra open-webui init
acmm-infra open-webui apply --experiment controlled_tool_use_multiscenario_v1
acmm-infra open-webui up
acmm-infra open-webui status
acmm-infra open-webui doctor
acmm-infra open-webui down
acmm-infra open-webui reset configuration --yes
```

`apply` is the full stopped-service baseline reconciler, matching the semantics used for
Hermes. It reconciles deployment files/environment first, starts Open WebUI temporarily,
waits for the backend `/ready` endpoint, reconciles persistent provider configuration through
Open WebUI's supported admin APIs, removes active repository-managed experiment Tools, and
then stops the provisioning service again. The command therefore leaves the ordinary agent
service stopped and experiment-independent.

Open WebUI stores provider and feature settings persistently in its application database, so
environment variables alone are not sufficient after those keys have been initialized. `apply`
explicitly reconciles the persistent Ollama provider to the ACMM Windows-Ollama route, forces
the OpenAI provider disabled while preserving inactive OpenAI connection details, and imports
the experiment-isolation settings through Open WebUI's supported admin configuration API.

The isolation baseline disables direct connections/integrations, external Tool and terminal
servers, web search, code execution/interpreter, memories and memory background review, image
generation/prompt generation, title generation, tag generation, follow-up generation,
autocomplete, search-query generation, and retrieval-query generation. These values are also
seeded through the container environment so the temporary provisioning startup begins in the
restricted state before the persistent database values are repaired.

`apply --experiment <experiment-id>` is the preferred full experiment reconciler. It
composes the baseline with the selected apparatus and reconciles that effective state during
one temporary provisioning-service lifetime. The narrower `apply-tools
--experiment <experiment-id>` path assumes the ordinary service is already running; with
`WEBUI_AUTH=false`, it obtains the local system-admin session and reconciles only the Tools
owned by the selected `experiments/<experiment-id>/apparatus.yaml` through Open WebUI's
supported Tool API. ACMM does not edit the Open WebUI SQLite database directly.

Because the Tool-only path works through the live Tool API, the ordinary Open WebUI service
must already be running. The command creates missing selected Tools, preserves exact Tools,
repairs source/name/description drift, removes stale apparatus from a previously selected
experiment, and records the selected apparatus SHA-256 with application scope `tools`.
`apply-experiment <experiment-id>` remains a compatibility alias for the full
`apply --experiment <experiment-id>` workflow.

The Experiment 1 `acmm_tools.py` source now lives under its own experiment directory. Its
Valves still provide controlled magic-number/retrieval/addition state for that experiment;
those per-run values remain runtime fixture state rather than static infrastructure state.

Runtime installation of packages declared through tool/function frontmatter is explicitly
disabled. The ACMM Tool therefore depends only on packages already present in the pinned
Open WebUI image. This keeps application provisioning deterministic and avoids modifying the
container Python environment during experiments.

## Readiness

`up` waits for Open WebUI's `/ready` endpoint before reporting success, so a subsequent
`status`, `doctor`, or execution-adapter request does not race backend startup.

`status` reports both deployment and live application observations when the service is
running, including persisted provider configuration and the model IDs returned by Open WebUI.
`doctor` verifies:

- Docker and Compose availability;
- exact deployed Compose baseline and ACMM-managed environment values;
- running container and exact pinned image identity;
- live Open WebUI version;
- successful backend authentication;
- persisted Ollama/OpenAI provider configuration;
- exact persistent experiment-isolation settings;
- availability of the configured experiment model; and
- when an experiment marker is active, presence and exact repository match of every Tool in
  the discovered experiment apparatus.

A baseline workflow is `init` -> `apply` -> `up` -> `doctor`. Before an experiment run, the
preferred full path is `apply --experiment <experiment-id>` while the service is stopped,
followed by `up` and `doctor`, which discovers that experiment from the deployment marker. During Tool-only development,
`apply-tools --experiment <experiment-id>` may be used while the service is running. A later
baseline `apply` removes active experiment Tools and returns Open WebUI to the
experiment-independent baseline.

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
