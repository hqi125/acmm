# Experiment Packages

Experiment Packages are the only research-experiment unit consumed by ACMM Core. Core owns the
measurement/runtime machinery; a package owns the scientific treatment.

## Ownership boundary

A package may own:

- `experiment.yaml` — the Contract;
- `runtime/` — fixtures, evaluators, and other experiment runtime code;
- `integration/` or another declared integration root — package-local capability setup;
- assets and deterministic scenario state;
- analysis configuration where supported; and
- study documentation.

Core does not contain a built-in research experiment catalog or experiment-specific tool code.

A minimal package can contain only the components its study requires. The frozen Minimal Agent
Baseline, for example, requires no capability integration.

## Manifest schemas

Package schema v0.1 remains supported because frozen packages use it. Package schema v0.2 adds the
ordered `integrations` component list. Package schema v0.3 adds top-level Experiment secret
declarations for trusted runtime/controller credentials while retaining v0.2 integration semantics.
These are package-format compatibility changes, not a second research-tool architecture.

A v0.2-or-later integration component declares:

```yaml
integrations:
  - id: example_capability
    api_version: "0.2"
    root: integration
    entrypoint: build_integration
    preset: optional-preset-name
    configuration:
      example: value
```

The package digest covers the declared component trees. Runtime and integration code is activated
only from the immutable installed snapshot, never from the mutable source checkout after install.

## Generic integration model

Core deliberately does not understand GitHub, MCP, databases, browsers, or any future concrete
capability. It understands only a package integration component and the generic integration API.

The ownership rule is:

> Core owns lifecycle and provenance. Integration owns mechanics. Experiment owns scientific meaning.

An integration entrypoint returns `experiment_integration`. For each supported infrastructure
subject it may provide either or both of:

- a profile composer, which transforms that subject's profile while preserving its concrete type;
- a manager composer, which wraps the normal subject manager while preserving its deployment root
  and Docker client identity.

Manifest order is composition order. This makes multiple integrations deterministic.

The integration receives a read-only context containing the experiment/integration identity,
preset, opaque package configuration, package digest, package root, infrastructure subject, and LLM
baseline. Package resources are resolved through safe package-relative paths.

Tool- or transport-specific implementation remains in the integration component. If a concrete
integration later becomes useful to several experiments, it can be extracted into a shared
integration distribution behind the same Core interface; package-local integrations remain valid for
experiments that require deep tailoring.

## Subject reconciliation

The package-aware manager construction path is:

```text
qualified subject baseline
    -> ordered integration profile composition
    -> normal subject manager
    -> ordered integration manager composition
```

Subject-specific host code may expose generic managed-configuration surfaces so integrations can
request desired state without patching a running deployment behind Core's back. The host manager is
still authoritative for apply/status/doctor/up/down semantics.

Returning to a baseline LLM state or detaching a package removes package-managed integration state.
Deployment markers contain package/integration provenance, not secret values.

## Lifecycle

Typical workflow:

```text
acmm package validate <package-directory>
acmm package install <package-directory>
acmm package apply <selector>
acmm run apparatus list
acmm run apparatus exec <action>
acmm run <selector> --preview
acmm run <selector>
acmm package install <package-directory> --upgrade
acmm package list
acmm package show <selector>
acmm package detach <selector>
acmm package uninstall <selector>
acmm package purge
acmm package purge --detach-all
```

`acmm run apparatus` is an independent nested operator/debug command family for package apparatus that may touch
the external experiment world. The bare family command is invalid; operators use `acmm run apparatus list` to
inspect package-defined operations and `acmm run apparatus exec <action>` to invoke one named free-form action
without repeating the experiment selector. Command-specific arguments stay on their owning subcommand rather than
being flattened into either `acmm run` or the apparatus root. Core does not infer or auto-run free-form actions
during install/apply/doctor. Actions may optionally publish structured progress events; the CLI renders them
immediately as `[step/total]` messages while preserving the package-owned action/result contract.

A runtime may also expose typed apparatus cleanup. `acmm run apparatus cleanup --execution <path>` passes one
persisted execution directory to the package cleanup implementation; `--preview` requests verification without
mutation. Cleanup may target completed (`experiment_execution.yaml`), interrupted
(`experiment_interruption.yaml`), or safely failed (`experiment_failure.yaml`) executions. Core owns only the
generic command/result contract. The package must derive its quarantine targets from the execution evidence and
enforce external-system identity/safety checks before deletion.

Free-form apparatus actions are separate from the optional typed execution lifecycle. A package lifecycle may
implement `session_initialize`, `run_prepare`, `run_observe`, `run_finalize`, and `session_finalize`. Core
invokes those hooks only at their fixed execution phases; they are not independently callable apparatus
actions. `run_finalize` is deliberately deferred until after the run result directory and evidence artifacts
have been persisted, allowing trusted cleanup to be evidence-last. If persistence fails, Core reports an
unpersisted finalization outcome so the package can quarantine external state instead of destroying it. Packages
that do not declare a typed lifecycle retain the legacy fixture prepare/observe/finalize behavior.

`acmm run` is strict by default. `--apply-if-needed` grants permission to reconcile only the selected
subjects, after which ACMM repeats the strict deployment preflight. `--start-if-needed` is a separate
permission to start a correctly deployed but stopped runtime.

Normal `package install` preserves immutable revisions side by side. `package install --upgrade` is
the explicit convergence path for an experiment ID that is already installed: ACMM stages the new
revision, moves only subjects that reference older revisions of that same experiment directly to the
replacement, and purges every older installed revision after successful reconciliation. It fails when
no prior revision is installed. If no old revision is attached, upgrade reduces to store replacement
without subject provisioning. Subjects attached to other experiment IDs are not touched.

`package uninstall` is always revision-targeted and therefore requires its positional selector.
`package purge` is the separate store-wide removal command. It remains strict about live deployment
provenance: attached revisions must be detached first or the operator must explicitly grant
`--detach-all`. `package list` numbers every installed revision and marks attached revisions with `*`;
`package show` lists the logical subject instances currently referencing the selected exact revision.

Normal installation is content-addressed. Source-directory location is not package identity: identical
content installed from different paths resolves to the same immutable revision, while different content
with the same experiment ID is preserved as distinct SHA-addressed revisions until the operator chooses
which one to apply or converges them with `--upgrade`.

## Provenance

A package-backed deployment and execution retain enough identity to answer which immutable scientific
revision ran. Current provenance includes:

- experiment ID;
- package schema version and package SHA-256;
- Contract SHA-256;
- runtime SHA-256 when a runtime component exists;
- ordered integration IDs and SHA-256 values; and
- infrastructure subject identity.

A mismatch is a preflight failure. ACMM does not silently reinterpret one installed package revision
as another.

## Runtime subject admission

An integration may bind run-scoped subject authority after the package runtime has prepared the
environment and before the subject receives the task. Core owns only generic orchestration: it calls
ordered integration `admit()` hooks, retains their opaque handles, and revokes completed admissions in
reverse order. Provider-specific credential semantics remain inside the integration.

An integration `admit()` hook is **failure-atomic for its own side effects**. If it creates, binds, or
otherwise changes external authority and then raises before returning a handle, the integration must
undo or safely terminate that authority itself. Core can unwind handles returned by earlier
integrations, but it cannot revoke a handle that was never returned. A failed cleanup must remain a
hard admission failure rather than being hidden by local fallback behavior.

Revocation semantics are capability-oriented rather than transport-specific: an integration may end
usable subject authority by removing local configuration, revoking provider authority, or both, as
long as its package contract records the distinction truthfully and fails closed when the promised
boundary cannot be established.

## Evidence boundary

Capability integrations configure how the subject receives a capability; adapters remain responsible
for collecting normalized conversation, native tool-call/tool-result, and final-response evidence.
Raw/native representations should be retained whenever the host exposes them. Normalization exists to
make cross-subject evidence analyzable, not to repair subject behavior.

The ownership boundary for tool-result failure semantics follows provenance. Core adapters may interpret
stable agent/runtime envelope semantics such as structured `ok`/`error` fields or terminal exit status.
An integration may additionally expose a tool-result error interpreter when its external provider has
provider-specific payload semantics that Core should not know. Integration interpretation is monotonic:
it may promote an otherwise non-error normalized result to an error, but it cannot clear an error already
established by the adapter. This keeps provider phrases and wire quirks out of Core while preserving one
normalized evidence surface for evaluators.

Future trusted pre/post-state observation can plug into the same generic package/runtime boundary
without making Core aware of the concrete external service.

## Package and integration secrets

Package schema v0.3 adds Experiment-scoped secret slots for trusted runtime/controller needs.
Package schema v0.4 adds an optional generic Campaign materialization declaration. A package may
declare `campaign.materialization.group_by` using planned-run coordinates (`run_index`,
`subject_id`, `scenario_id`, and/or `repetition`). Core freezes that grouping in Campaign v0.2
and executes runs subject-major inside each materialization slice. Provider-specific resource
lifetime, restoration, taint, and cleanup remain package/runtime concerns.
Integration-scoped secret slots introduced in schema v0.2 remain available for concrete capability
integrations. Secret declarations are part of immutable package identity; secret values and
machine-local source paths are not.

Local bindings are managed separately:

```text
acmm secret init
acmm secret validate
acmm secret upgrade
acmm secret bind experiment EXPERIMENT_ID SECRET_ID --file PATH
acmm secret bind integration EXPERIMENT_ID INTEGRATION_ID SECRET_ID --file PATH
acmm secret list
acmm secret unbind experiment EXPERIMENT_ID SECRET_ID
acmm secret unbind integration EXPERIMENT_ID INTEGRATION_ID SECRET_ID
```

The default binding registry is `~/.config/acmm/secret-bindings.yaml`; recommended secret source
files live under `~/.config/acmm/secrets/`. Registry YAML is a versioned data format validated
against the bundled JSON Schema selected by its mandatory `schema_version`. Ordinary Core
operations require the registry version that Core currently supports and never migrate an older
registry implicitly. `acmm secret upgrade` is the only supported registry transformation path.
`acmm secret validate` additionally checks every bound source file for existence, regular-file
status, safe permissions, readability, and a non-empty value without displaying secret content.

A file-backed secret must be owner-only on POSIX systems. Runtime code can resolve only Experiment
secret slots declared by its package; integration code can resolve only secret slots declared by
that integration. Core does not interpret what either secret means to the package. `unbind` changes
only registry metadata and never deletes the referenced secret source file.

## Shared integration resources

An integration may also provide one package-level shared resource lifecycle. Core can reconcile,
inspect, diagnose, stop, and remove that resource once per package revision, independently of the
three subject managers. The resource receives a package-scoped runtime state directory and the same
opaque integration configuration/secret resolver as other integration hooks.

This mechanism intentionally does not define Docker, MCP, databases, or network services. A concrete
integration owns those mechanics. One integration may therefore run a shared local service while a
future preset for the same capability uses a hosted service without changing the Core integration
language.
