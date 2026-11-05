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
ordered `integrations` component list. This is a package-format compatibility requirement, not a
second research-tool architecture.

A v0.2 integration component declares:

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
acmm run <selector> --preview
acmm run <selector>
acmm package detach <selector>
acmm package uninstall <selector>
```

`acmm run` is strict by default. `--apply-if-needed` grants permission to reconcile only the selected
subjects, after which ACMM repeats the strict deployment preflight. `--start-if-needed` is a separate
permission to start a correctly deployed but stopped runtime.

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

## Evidence boundary

Capability integrations configure how the subject receives a capability; adapters remain responsible
for collecting normalized conversation, native tool-call/tool-result, and final-response evidence.
Raw/native representations should be retained whenever the host exposes them. Normalization exists to
make cross-subject evidence analyzable, not to repair subject behavior.

Future trusted pre/post-state observation can plug into the same generic package/runtime boundary
without making Core aware of the concrete external service.
