# Experiment Package Architecture

## Status

This document defines the EP-0 architecture for ACMM Experiment Packages. It is the design
baseline for the package loader, runtime boundary, apparatus refactor, provenance model, and later
externalization work.

The design deliberately preserves the existing ACMM Experiment Contract, runtime execution model,
subject adapters, qualified subject catalog, LLM-baseline workflow, and agent infrastructure. The
purpose of Experiment Package work is to move experiment-specific ownership out of the main
repository without redesigning those stable layers unnecessarily.

## Purpose

An **Experiment Package** is a self-contained research artifact containing the material specific to
one experiment that ACMM needs to validate, deploy, execute, evaluate, analyze, and reproduce.

The package is not a Python distribution and is not installed with `pip`. ACMM infrastructure owns
package acquisition and deployment. The initial package form is a validated filesystem directory;
future archive, registry, Git-release, or Python-distribution transports may be added without
changing the logical package contract.

The architectural goal is:

```text
ACMM main repo
    |
    +-- Experiment Contract/schema validation
    +-- package loader and staging
    +-- subject catalog and LLM baselines
    +-- subject adapters
    +-- agent infrastructure/deployment engines
    +-- runtime orchestration and evidence normalization
    +-- ACMM scoring and generic analysis
    +-- persistence and provenance
    |
    v
Experiment Package
    |
    +-- experiment.yaml
    +-- experiment-specific runtime code
    +-- experiment-specific apparatus/configuration
    +-- experiment-specific analysis declarations/resources
    +-- experiment assets and documentation
```

The main repo knows **how to run an experiment package**. It should not need a source-code edit to
know **which experiments exist**.

## Non-goals

Experiment Package work does not move or redesign subject adapters. Adapter externalization, if it
is ever useful, is a separate packaging problem.

EP-0 also does not define:

- a public package registry;
- remote package download;
- `pip` installation of experiments;
- package-specific Python dependency installation;
- a new subject/model identity system;
- a replacement for the Experiment Contract;
- a replacement for the existing ACMM execution registry internals;
- arbitrary package-controlled changes to qualified agent baselines; or
- secret/credential storage inside an experiment package.

## Terminology

The following terms have distinct responsibilities.

### Experiment Package

The complete external research artifact. A package binds the Contract, executable experiment
runtime, subject-specific apparatus, analysis resources, assets, and documentation.

### Package Manifest

`experiment-package.yaml` at package root. It identifies the package components and declares the
Experiment Package schema/runtime API versions required to consume them. The main repo owns each
versioned manifest schema as a standalone JSON Schema file under
`src/acmm/experiment_package/schemas/`; packages conform to that schema but do not carry their own
copy.

### Experiment Contract

The existing ACMM experiment declaration, normally `experiment.yaml`. It remains the scientific
and normative description of subjects, scenarios, requirements, opportunities, checks, evidence
policy, reset behavior, run ordering, pacing, and termination.

### Experiment Runtime

Trusted experiment-specific Python code providing fixtures, evaluators, and harness capability
metadata through a narrow public ACMM API. It does not own subject adapters or infrastructure.

### Experiment Apparatus

Experiment-specific deployment/configuration layered onto a qualified subject baseline. Apparatus
may include tools, plugins, MCP configuration, or an explicitly manipulated agent feature when the
experiment requires it. The main repo owns the mechanism that validates and applies those deltas.

### Experiment Analysis

Experiment-specific analysis declarations, designs, scripts, or other resources. Generic ACMM
analysis remains in the main repo.

### Package Source

The researcher-selected directory containing an Experiment Package before ACMM acquisition.

### Staged Package

The validated, digest-identified snapshot copied into the ACMM runtime root. Deployment and
execution consume the staged package rather than continuing to depend on mutable source files.

## Ownership boundary

The package/main-repo boundary is intentionally strict.

| Concern | Main repo | Experiment package |
| --- | --- | --- |
| Contract schema and semantics | Owns | Uses |
| Package schema/loader/stager | Owns | Conforms |
| Subject catalog | Owns | References subject IDs |
| LLM baseline catalog/rebasing | Owns | Does not implement |
| Subject adapters | Owns | Does not implement |
| Evidence normalization | Owns | Consumes normalized evidence in evaluators |
| Agent baseline profiles | Owns | Supplies experiment deltas only |
| Docker/service lifecycle | Owns | Does not implement agent lifecycle |
| Tool/MCP/feature deployment mechanism | Owns | Declares experiment-specific apparatus |
| Experiment scenarios and requirements | Validates | Owns |
| Experiment fixtures/evaluators | Provides API | Owns when experiment-specific |
| ACMM scoring/aggregation | Owns | Does not reimplement |
| Generic analysis | Owns | Uses |
| Supplementary experiment analysis | Invokes/validates | Owns |
| Package assets | Does not own | Owns |
| Credentials/secrets | Resolves outside package | Must not store |
| Results/runtime mutable state | Owns locations/lifecycle | Must not store |

A package may depend on stable ACMM package APIs, but it must not reach into CLI code,
`acmm_infra` implementation internals, subject adapters, deployment managers, or repository-relative
paths.

## Initial directory model

The preferred v0.1 shape is deliberately shallow:

```text
<experiment-package>/
    experiment-package.yaml
    experiment.yaml
    README.md

    runtime/
        __init__.py
        fixtures.py
        evaluators.py
        ...

    apparatus/
        hermes/
            apparatus.yaml
            ...
        open-webui/
            apparatus.yaml
            ...
        agent-zero/
            apparatus.yaml
            ...

    analysis/
        ...

    assets/
        ...
```

Only `experiment-package.yaml` and `experiment.yaml` are inherently required by the package model.
Other directories are present only when the experiment needs them.

Keeping `experiment.yaml` at package root preserves the current authoring convention and avoids a
one-file `contract/` directory that adds little information.

### `runtime/`

`runtime/` is a Python package loaded by ACMM under an ACMM-generated private module namespace. It
may contain as many internal modules as the experiment needs. It is not installed into the Python
environment and should not require its package name to be globally unique.

### `apparatus/`

Each child directory is keyed by ACMM's infrastructure-subject identifier, not by Contract subject
ID. This allows one apparatus description to serve the qualified subject bound to that
infrastructure integration.

A package may omit apparatus entirely. This is important for a minimal-agent baseline whose
scientific treatment requires no experiment-specific deployment delta.

### `analysis/`

This directory contains only analysis material specific to the experiment. A package should not
copy generic ACMM export, availability, aggregation, or scoring code merely to become
self-contained.

### `assets/`

Static task material, seed data, repository templates, controlled documents, or similar
experiment-owned inputs belong here. Live credentials, mutable external state, and execution
results do not.

## Package manifest v0.1 direction

EP-1 should formalize a schema, but EP-0 adopts the following minimal shape as the design target:

```yaml
schema_version: "0.1"
experiment_id: github_repository_operations_v1

contract: experiment.yaml

runtime:
  api_version: "0.1"
  root: runtime
  entrypoint: build_runtime

apparatus:
  hermes: apparatus/hermes
  open-webui: apparatus/open-webui
  agent-zero: apparatus/agent-zero

analysis:
  supplementary:
    - kind: interaction
      configuration: analysis/interaction_analysis.yaml
```

Sections that are not required by an experiment may be omitted. For example, a no-apparatus
baseline can omit `apparatus`, and an experiment with no supplementary analysis can omit
`analysis`.

The manifest intentionally does **not** duplicate scenario, subject, requirement, run-protocol, or
evidence-policy fields from `experiment.yaml`.

### Identity

`experiment_id` must match `experiment.id` in the Contract and any experiment identity embedded in
subject apparatus declarations. The existing versioned experiment-ID convention remains the
human-readable scientific identity, for example `github_repository_operations_v1`.

EP-0 does not introduce a second semantic package version number. Exact package revision is
identified by its canonical digest. This avoids competing meanings between an experiment `_v1`, a
package `version: 1`, and the actual bytes used during acquisition.

## Executable runtime boundary

Experiment Packages are allowed to contain executable Python code. This is necessary because real
experiments require custom fixtures and evaluators that cannot be represented cleanly in YAML.

The code is **trusted research code**. Manifest validation does not sandbox it. Loading the runtime
entrypoint is equivalent to executing local code selected by the researcher. Initial ACMM commands
must therefore consume explicit local package paths rather than automatically downloading and
executing remote packages.

### Public API rule

Package runtime code must depend on a deliberately public Experiment API rather than arbitrary
main-repo internals. EP-2 should introduce a facade such as:

```text
acmm.experiment_api
```

The facade should expose the small set of stable runtime concepts experiment code legitimately
needs, including:

- `experiment_runtime`;
- `experiment_runtime_context`;
- `harness_capabilities`;
- fixture/evaluator protocols and result/context data structures; and
- normalized evidence helper functions needed by evaluators.

Existing pilot imports from `acmm.runtime.interfaces`, `acmm.runtime.registry`,
`acmm.harness.capabilities`, and `acmm.evidence.*` provide the initial evidence for what belongs in
that public facade.

Package code must not import `acmm_infra`, CLI modules, concrete adapters, subject-manager code, or
repository-private pilot modules.

### Runtime entrypoint

The manifest names one callable from `runtime/__init__.py`. The initial API should conceptually be:

```python
def build_runtime(
    context: experiment_runtime_context,
) -> experiment_runtime:
    ...
```

`experiment_runtime_context` supplies package identity and safe access to the staged package root.
It does **not** provide subject adapters or infrastructure managers.

The returned `experiment_runtime` supplies only experiment-owned execution contributions:

```text
experiment_runtime
    capabilities
    fixtures
    evaluators
```

The main repo then composes those contributions with the selected subject adapters and core-owned
subject provenance/configuration to produce the existing internal `execution_registry` used by the
execution engine.

This is a deliberate improvement over current built-in pilot builders, which receive subject
adapters directly. Adapters are not an experiment concern and should not cross the package API.

### Runtime dependencies

For v0.1, package runtime code should use only:

- the Python standard library; and
- the public ACMM Experiment API.

Silent package-specific third-party Python dependencies are not allowed. If real experiments later
require such dependencies, ACMM should add an explicit infrastructure-managed dependency mechanism
rather than asking researchers to `pip install` packages manually.

## Apparatus boundary

Current apparatus resolution assumes:

```text
<main-repo>/infrastructure/<subject>/experiments/<experiment-id>/
```

External packages require the infrastructure layer to consume a **resolved apparatus root** instead
of reconstructing a path from `repo_root + experiment_id`.

The target relationship is:

```text
package loader
    |
    +-- resolves staged package apparatus/hermes
    |
    v
Hermes infrastructure
    |
    +-- validates Hermes apparatus schema
    +-- resolves apparatus-local source paths
    +-- applies supported experiment deltas
```

The same pattern applies to Open WebUI and Agent Zero.

Apparatus-internal source paths must be relative to the apparatus directory (or another explicitly
documented package-relative root), never to the ACMM main repository. A package must remain valid
when its source directory is moved elsewhere.

### Experimental agent features

The new research program intentionally manipulates agent-side variables. Experiment apparatus is
therefore broader than "tool files." It is the experiment-specific delta layered onto a qualified
agent baseline.

For example, an apparatus may eventually request:

```text
qualified minimal-agent baseline
    + GitHub MCP
```

or:

```text
qualified minimal-agent baseline
    + GitHub MCP
    + native memory enabled
```

The package owns the requested treatment. The main repo owns the supported mechanism for applying
it. Apparatus must not gain an unrestricted "patch arbitrary agent files" escape hatch merely to
make feature experiments easy.

This distinction keeps the manipulated variable visible and validates that all changes remain on
known experimental surfaces.

## Package acquisition and staging

ACMM should not keep executing directly from the mutable source directory supplied by a researcher.
The package lifecycle should include an explicit acquisition step:

```text
source package path
        |
        v
structural validation
        |
        v
Contract/manifest cross-validation
        |
        v
canonical package digest
        |
        v
stage immutable snapshot under ACMM runtime root
        |
        +------------------+
        |                  |
        v                  v
apparatus deployment   runtime execution
```

A suitable conceptual runtime location is:

```text
<ACMM_RUNTIME_ROOT>/_experiment_packages/
    <experiment_id>/
        <package_sha256>/
            ...staged package snapshot...
```

The exact directory spelling is an EP-1/EP-3 implementation detail.

### Why staging is required

Staging provides several properties that a source-path-only design cannot guarantee:

1. deployment and execution use exactly the bytes that were validated and hashed;
2. editing the source package during a run cannot silently mutate the active experiment;
3. `status` and `doctor` can reconstruct active experiment state without requiring the user to
   remember or re-enter an external source path;
4. all three agent deployments can be checked against the same package digest;
5. a package source may be moved or deleted after successful staging without changing an already
   deployed experiment; and
6. package deployment remains an ACMM infrastructure concept rather than a Python installation
   workflow.

The staged package should be treated as immutable ACMM-managed state. Re-acquiring changed source
bytes produces a different digest and therefore a distinct staged snapshot.

## Path and filesystem rules

All manifest-selected resources and apparatus source paths must remain inside the package root or
their documented component root.

For the first package schema, ACMM should reject path traversal and symlink-based escapes rather
than attempting to model external filesystem dependencies. This makes digesting and staging
unambiguous.

Generated caches such as `__pycache__`, editor state, Git metadata, result directories, and similar
non-source artifacts must not participate in package staging or canonical hashing. The exact ignore
set should be small, deterministic, and owned by ACMM rather than package-defined ignore files.

## Digests and provenance

Every acquired package receives one canonical package SHA-256 digest. The digest should cover all
managed package source files after applying ACMM's fixed exclusions.

Executions should additionally preserve component digests where they improve diagnosis and
comparability, including at least:

- package digest;
- Experiment Contract digest;
- runtime digest when runtime exists;
- per-subject apparatus digest when apparatus exists; and
- experiment-specific analysis/configuration digest when applicable.

The package digest is the exact artifact identity. Component digests explain which part changed.

A future execution manifest should therefore be able to say, conceptually:

```text
experiment_id: github_repository_operations_v1
package_schema_version: 0.1
runtime_api_version: 0.1
package_sha256: ...
contract_sha256: ...
apparatus:
    hermes: ...
    open-webui: ...
    agent-zero: ...
```

Active deployment markers should record the package digest as well as the experiment ID. Cohort
readiness should require selected subjects to converge on both values, not merely on a matching
experiment ID.

## Credentials and live external resources

Real-tool experiments will eventually require credentials and live services, for example a GitHub
token or repository under test.

Secrets must never be committed to, hashed as part of, or staged from an Experiment Package.
Packages may later declare named credential/resource requirements, but resolution belongs to ACMM
infrastructure or an explicit external configuration surface.

Similarly, an experiment may package deterministic seed assets or repository templates while the
live remote repository created from them remains mutable experiment state managed by fixtures or
infrastructure.

The exact credential/resource declaration schema is intentionally deferred until the first real
external-tool experiment provides concrete requirements.

## Analysis ownership

Generic ACMM analysis remains main-repo code. This includes ordinary score aggregation,
availability analysis, export mechanics, and other analyses that are meaningful across experiments.

Experiment-specific analysis belongs to the package. The existing interaction-analysis pattern is
a useful example: the generic R analysis engine can remain core-owned while the experiment-specific
factor/design declaration moves into the package.

EP-0 permits package-owned custom analysis code in principle, but does not freeze a custom analysis
entrypoint API. That API should be introduced only when a real study cannot be expressed through
core analysis plus package-owned configuration. This avoids designing a second plugin system before
it is needed.

## Validation phases

Package validation should occur in layers so failures remain understandable.

### Phase 1: source structure

Validate that:

- package root exists;
- `experiment-package.yaml` is readable;
- schema version is supported;
- required manifest fields have valid types;
- all declared paths are safe package-relative paths; and
- required files/directories exist.

No package Python code is executed in this phase.

### Phase 2: Contract and identity

Load the existing Experiment Contract and validate:

- Contract schema/semantics;
- manifest `experiment_id` equals Contract experiment ID; and
- apparatus identities, when present, agree with the same experiment ID.

### Phase 3: package snapshot

Compute the canonical digest and stage the exact managed source tree. Revalidate the staged copy or
verify that its digest matches the acquired source digest.

### Phase 4: runtime API

Only after the package is staged should ACMM load trusted runtime code. Validate:

- supported runtime API version;
- entrypoint exists and is callable;
- returned object has the required public API type; and
- runtime declarations are internally valid.

### Phase 5: harness compatibility

Use the runtime's capabilities with the existing Contract compatibility validator. This preserves
the current contract-versus-harness safety check.

### Phase 6: infrastructure compatibility

Validate that each required subject has a registered core subject binding and that any declared
apparatus uses features supported by that subject's infrastructure integration.

These phases can support both a standalone `validate` command and preflight validation for
apply/run.

## CLI direction

Exact syntax is deferred, but the command model should become package-oriented for new studies.
Conceptually:

```text
acmm experiment validate <package-path>
acmm env apply <package-path>
acmm run <package-path>
```

After a package has been staged and deployed, existing convenience behavior should remain possible:

```text
acmm run
```

can infer the one converged active package from selected deployment contexts, now using experiment
ID plus package digest rather than a built-in catalog entry.

Similarly, `status` and `doctor` should continue to work without requiring the package source path.
This is one of the reasons staged package state is part of the design.

The existing ID-based commands may remain as a legacy compatibility path while old experiments are
still built into the main repo.

## Relationship to the current execution registry

The existing `execution_registry` remains a useful internal object. EP-0 does not replace it.

Current built-in pilot flow:

```text
catalog lookup
    -> pilot registry_builder(subject_adapters)
    -> execution_registry
```

Target package flow:

```text
staged Experiment Package
    -> build_runtime(context)
    -> experiment_runtime

core subject resolution
    -> subject adapters
    -> core subject provenance/configuration

experiment_runtime + core subject components
    -> execution_registry
    -> existing execution engine
```

This preserves the tested execution machinery while removing adapter ownership from experiment
code.

## Relationship to LLM Rebasing

Experiment Packages reference qualified subject IDs from the core subject catalog. They do not
select provider URLs, instantiate models, or implement LLM baseline resolution.

This allows LLM Rebasing to remain a core infrastructure qualification workflow. New experiments
can use the promoted baseline subjects without embedding current model configuration in package
runtime code or apparatus unless a scientifically meaningful experiment explicitly requires a
model-related treatment.

Package validation and execution should therefore help detect stale model leakage rather than
creating another place where model names are copied as defaults.

## Transition from built-in experiments

Externalization should be incremental.

During transition, ACMM may support two sources:

```text
legacy built-in experiment
    -> current catalog/pilot/main-repo apparatus path

new experiment
    -> Experiment Package loader/stager
```

New research studies should use packages once the package path is operational. Existing historical
experiments should not be rewritten merely to prove the abstraction.

One legacy experiment may be copied into package form later as an integration fixture, but that
migration is a framework test rather than a change to its historical scientific record.

When the package mechanism is mature, old research experiments can be archived outside the main
repo while the core repository retains only small fixtures needed for regression tests.

## EP-0 decisions

The following decisions are considered part of the initial architecture unless implementation work
reveals a concrete contradiction:

1. Experiment Packages may contain executable Python runtime code.
2. Packages are not Python distributions and are not installed with `pip` in the initial design.
3. ACMM infrastructure owns package acquisition, staging, and deployment.
4. Deployment/execution consume a staged digest-identified package snapshot.
5. Subject adapters, subject bindings, LLM baselines, and agent lifecycle remain main-repo concerns.
6. Package runtime code receives no subject adapters.
7. Package runtime code depends on a narrow public ACMM Experiment API.
8. `experiment.yaml` remains the Experiment Contract and stays separate from package metadata.
9. Apparatus paths become package/component-relative rather than main-repo-relative.
10. Apparatus may represent deliberate agent-feature treatments as well as tool deployment.
11. The package digest is the exact package revision; no duplicate semantic package version is
    introduced in v0.1.
12. Explicit source paths are used for initial package acquisition; automatic remote code retrieval
    is out of scope.
13. Secrets and live credentials are never package contents.
14. Generic analysis stays in the main repo; experiment-specific analysis material stays in the
    package.
15. Legacy built-in experiments may coexist with packages during migration.
16. The main repo owns each Experiment Package manifest schema as a standalone, versioned JSON
    Schema file; packages reference a schema version and do not carry schema copies.

## Deferred design points

These topics should remain open until a concrete experiment requires them:

- credential/resource requirement declaration;
- package-specific third-party dependency provisioning;
- custom executable analysis API;
- remote package registries/distribution;
- archive/package transport formats;
- shared external service provisioning across agents;
- whether any package metadata should record a source Git commit in addition to the digest; and
- final archive layout for the historical built-in experiments.

Deferring these is intentional. None is necessary to establish a clean package boundary.

## EP-1 implementation target

EP-1 should implement only the package model and acquisition boundary, not the full apparatus
externalization at once.

A successful EP-1 should be able to:

1. load an `experiment-package.yaml` from an explicit directory;
2. validate its initial schema and safe paths;
3. load and validate its existing `experiment.yaml` Contract;
4. enforce experiment-ID consistency;
5. enumerate declared runtime/apparatus/analysis components without executing package code;
6. compute deterministic package/component digests;
7. stage a digest-identified package snapshot beneath the runtime root;
8. reopen and verify the staged package independently of the source directory; and
9. expose a small typed package model that later runtime, infrastructure, and CLI work can consume.

EP-1 should not yet replace the built-in experiment catalog, dynamically load runtime code, or
refactor subject apparatus application. Those belong to subsequent steps and can be tested against
the stable package model created here.

## EP-1 implementation checkpoint

EP-1 implements the package model and acquisition/staging boundary described above without changing
legacy experiment execution or subject apparatus application.

The initial implementation lives under `acmm.experiment_package` and provides:

- typed package, runtime, apparatus, and supplementary-analysis component models;
- `experiment-package.yaml` loading through the standalone versioned `experiment-package-v0.1.schema.json`;
- existing Experiment Contract schema/semantic validation and experiment-ID cross-validation;
- safe package-relative resource resolution;
- rejection of managed symlinks and declared resources excluded from package management;
- deterministic package, Contract, runtime, apparatus, and analysis-configuration SHA-256 values;
- fixed exclusion of generated/editor/VCS/result artifacts from package hashing and staging;
- digest-addressed staging beneath `<runtime-root>/_experiment_packages/`;
- staged-package reopening and digest verification independent of the original source directory; and
- idempotent reacquisition of an already staged package snapshot.

EP-1 intentionally does not load package runtime Python, compose subject adapters, change the
built-in experiment catalog, or apply package apparatus. Those remain subsequent Experiment Package
steps.

## EP-2 implementation checkpoint

EP-2 establishes the executable Experiment Runtime boundary while preserving the existing subject
adapter and legacy catalog ownership model.

The public package-facing API is `acmm.experiment_api` version `0.1`. It exposes only the runtime
concepts experiment code needs:

- `experiment_runtime` and `experiment_runtime_context`;
- `harness_capabilities`;
- fixture/evaluator protocols and their context/result structures; and
- normalized evidence access/text helpers used by experiment evaluators.

The facade deliberately does not export subject adapters, `execution_registry`, infrastructure
managers, subject catalogs, or CLI implementation details. This API boundary is a dependency
contract for package authors rather than a Python sandbox; Experiment Package runtime remains
trusted local research code.

Runtime activation now:

1. accepts only a package located in the ACMM staged-package layout;
2. rechecks the package and runtime-component digests immediately before code import;
3. imports `runtime/__init__.py` under an ACMM-generated private module namespace;
4. supports ordinary relative imports inside the runtime directory without installing the package;
5. constructs an `experiment_runtime_context` containing package identity and staged-root access;
6. invokes the manifest-selected entrypoint;
7. requires the entrypoint to return `experiment_runtime`;
8. validates fixture/evaluator declarations and callable protocol surfaces; and
9. runs the existing Contract-versus-harness compatibility validation against the returned
   capabilities.

`experiment_runtime_context.resolve_resource()` provides a convenient checked package-relative
resource lookup for package-owned assets. The runtime remains trusted Python, so this helper is not
presented as a security boundary.

The returned runtime contains only experiment-owned contributions:

```text
experiment_runtime
    capabilities
    fixtures
    evaluators
```

Core code composes those contributions with core-owned subject adapters and subject configuration
overlays through `compose_execution_registry()`. Subject adapters therefore never cross the public
Experiment API boundary.

As a compatibility probe, the built-in `magic_number` pilot now constructs the same
`experiment_runtime` contribution model and then composes it into the legacy `execution_registry`.
Its catalog identity, Contract, adapter ownership, and historical execution path remain unchanged.
This demonstrates that the package runtime model can represent an existing real ACMM experiment
without requiring a redesign of the execution engine.

EP-2 does not yet make the CLI discover or run external packages, replace the built-in experiment
catalog, or apply package-owned apparatus. Those remain later package milestones. The next major
boundary is EP-3: make agent infrastructure consume resolved apparatus roots from staged packages
instead of reconstructing main-repository experiment paths.


## EP-3 implementation checkpoint

EP-3 establishes the Experiment Apparatus boundary without replacing legacy experiment discovery
or the existing agent lifecycle.

Staged package apparatus is now resolved through `load_staged_experiment_apparatus()`. Resolution:

1. requires the package to be in the ACMM staged-package layout;
2. rechecks the full package digest;
3. rechecks the selected apparatus component digest; and
4. returns the already typed apparatus component/root from the validated package model.

The agent infrastructure layer now accepts a resolved apparatus root through:

- `with_hermes_apparatus()`;
- `with_open_webui_apparatus()`; and
- `with_agent_zero_apparatus()`.

These functions validate the apparatus identity and interpret managed source paths relative to the
apparatus root. Package apparatus therefore uses paths such as `plugins/acmm-tools` or
`tools/acmm_tools.py` rather than paths rooted in the ACMM repository.

Managed Hermes plugins, Open WebUI tools, and Agent Zero plugins now carry an optional explicit
`source_root`. Their `source` remains a safe relative path. Agent managers resolve managed content
against `source_root` when present and against their historical main-repo root otherwise. This
keeps package apparatus portable without weakening path-containment checks or storing absolute
paths as package declarations.

The existing `with_*_experiment()` functions remain as compatibility wrappers for built-in legacy
experiments. Their source paths and `source_root=None` behavior are unchanged, so the historical
main-repo apparatus layout continues to work during migration. Package apparatus provenance uses
the component digest already carried by the staged package model, while the existing ID-based
digest helper remains available for legacy callers.

As an integration proof, all three agent managers have tests that consume package-local tool/plugin
content from an ACMM-staged snapshot after the original source package has been removed. This
demonstrates that deployment-facing source resolution no longer depends on the main repository or
the original package source directory.

EP-3 intentionally does not yet make `acmm env`, `status`, `doctor`, or `acmm run` discover an
external package automatically. It also does not yet persist package identity in deployment state.
Those higher-level lifecycle/provenance changes belong to the next package milestones.

## EP-4 implementation checkpoint

EP-4 establishes digest-bearing Experiment Package provenance in deployed subject state and makes
passive deployment reconstruction package-aware without introducing package-oriented CLI commands
yet.

Package provenance is represented explicitly by:

- `experiment_package_provenance`, which records experiment ID, package schema version, package
  SHA-256, Contract SHA-256, and runtime SHA-256 when runtime code exists; and
- `experiment_deployment_provenance`, which binds that package identity to one infrastructure
  subject and records its apparatus component SHA-256 when the package declares apparatus for that
  subject.

There is intentionally still no independent semantic package version. The experiment ID remains the
human-readable scientific identity and the package SHA-256 remains the exact package revision.

The existing `.acmm-experiment-apparatus.json` deployment marker advances to schema `0.3`. The file
name is retained for backward compatibility even though schema `0.3` now serves as the broader
experiment deployment-context marker. Legacy schema `0.1` and `0.2` markers remain readable.
Package-backed markers can additionally persist:

```text
package schema version
package SHA-256
Contract SHA-256
runtime SHA-256, when present
infrastructure subject
apparatus SHA-256, when present
```

The marker validates that an apparatus SHA-256, when present, agrees with the existing
`source_sha256` field. A package deployment may also legitimately record no apparatus SHA-256 and no
`source_sha256`. This is required for realistic no-apparatus studies such as the planned minimal
agent baseline; ACMM does not require a dummy tool merely to give that experiment a deployable
identity.

Package-aware subject-manager construction now:

1. resolves the subject's LLM baseline from qualified subject IDs in the package Contract;
2. applies staged package apparatus when that subject declares apparatus;
3. otherwise retains the qualified baseline profile unchanged;
4. carries package deployment provenance into the normal agent manager; and
5. persists the package/component identity when experiment state is written.

Passive manager reconstruction reads the persisted package digest, reopens the corresponding staged
snapshot from `<runtime-root>/_experiment_packages/`, and revalidates the package schema identity,
Contract digest, runtime digest, infrastructure subject, apparatus digest, and deployed source
digest before rebuilding the manager. The original external package source directory is therefore
not required by `status`, `doctor`, or runtime lifecycle reconstruction.

Cohort status now carries package SHA-256 in addition to experiment ID. Context convergence requires
subjects to agree on package revision whenever package provenance is present. Two subjects reporting
the same `experiment_id` but different package SHA-256 values are `divergent`; mixing package-backed
and legacy deployments under the same experiment ID is also divergent. A package-backed subject
with no apparatus is considered apparatus-matched when both its expected and observed apparatus
identities are absent.

Deployment-context discovery also exposes observed package digests and accepts package-backed
subjects that intentionally declare no apparatus. Existing legacy ID-based apparatus preflight is
left intact; package-aware run preflight and package-path command selection belong to EP-5.

As a proof of independence, EP-4 tests reconstruct a staged package using an experiment ID that is
not registered in the built-in experiment catalog. This demonstrates that deployed package identity
is now sufficient for passive reconstruction rather than merely annotating the historical catalog
path.

EP-4 does not yet add `acmm env apply <package-path>`, `acmm run <package-path>`, package discovery,
or package provenance to execution result manifests. Those are integration responsibilities for the
next milestone rather than deployment-state concerns.

## EP-5 implementation checkpoint

EP-5 exposes the Experiment Package architecture through the normal ACMM researcher workflow while
preserving the built-in experiment catalog as a legacy compatibility path.

The top-level CLI now provides explicit package lifecycle commands:

```text
acmm package validate <package-directory>
acmm package acquire <package-directory> [--runtime-root <root>]
```

`validate` parses the package manifest, validates the Contract, checks managed paths, and computes
component/package digests without staging or executing package runtime code. `acquire` performs the
same validation and then stages the immutable digest-addressed snapshot under
`<runtime-root>/_experiment_packages/`. Both commands consume only explicit local filesystem paths;
EP-5 does not introduce remote package discovery or downloading.

Environment application accepts a package source directly:

```text
acmm env apply --package <package-directory>
```

The command acquires the package first, resolves its qualified Contract subjects and LLM baselines,
and builds package-aware subject managers. `--package` and legacy `--experiment` are mutually
exclusive, and neither package Contracts nor built-in experiment Contracts may be overridden by an
independent `--llm-baseline` selection. Package-backed cohort application verifies the exact package
SHA-256 after each subject is reconciled. Packages that declare no apparatus remain valid experiment
contexts and therefore do not require a synthetic tool layer.

Experiment execution accepts either an explicit package source or a package already represented by
deployed subject state:

```text
acmm run --package <package-directory> --preview
acmm run --package <package-directory>
acmm run --preview
```

The explicit `--package` path validates/acquires the source, activates runtime code only from the
staged snapshot, and composes the returned experiment runtime with core-owned subject adapters and
LLM provenance overlays. A positional legacy Contract and `--package` cannot be combined.

When neither a Contract nor `--package` is supplied, deployment-context inference now recognizes
package provenance. If the selected contexts converge on one experiment ID and one package SHA-256,
ACMM reopens that staged snapshot and runs it even when the experiment ID is absent from the built-in
catalog. Mixed legacy/package state or differing package revisions are rejected rather than silently
choosing one source.

Package-aware execution preflight verifies the exact staged deployment identity for every selected
subject:

- experiment ID and full `experiment` application scope;
- package schema version and package SHA-256;
- Contract and runtime component SHA-256 values;
- infrastructure/apparatus subject identity; and
- apparatus component/deployed-source SHA-256 when apparatus exists.

A package that intentionally declares no apparatus passes when both the package provenance and
deployment state record no apparatus for that subject. Preview mode retains the existing warning-only
preflight behavior and never contacts subject runtimes. Package preview does activate the package's
trusted runtime code from the staged snapshot because ACMM must validate harness compatibility and
construct the experiment-owned fixture/evaluator declarations; `acmm package validate` remains the
non-executing package inspection path.

Completed and interrupted execution manifests remain schema `0.1` and now optionally carry an
`execution.package` mapping containing the package schema version, package SHA-256, Contract
SHA-256, and runtime SHA-256. Built-in legacy executions omit this mapping, preserving their
historical manifest shape while package-backed results bind execution evidence to the exact staged
research artifact.

EP-5 deliberately does not migrate the historical experiments out of the main repository or remove
the built-in experiment catalog. The next milestone, EP-6, should prove the boundary by moving one
representative experiment completely outside the main repo and exercising validation, acquisition,
environment application, preview/execution resolution, and analysis from that external package.
