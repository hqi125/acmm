# Experiment Package Architecture

## Purpose

An Experiment Package is the sole research-experiment identity used by current ACMM. Core owns the
measurement/runtime platform; packages own study-specific scientific and executable material.

A package is a self-contained, versioned research artifact containing everything specific to one
experiment that ACMM needs to validate, stage, deploy, execute, evaluate, analyze, and reproduce.
Package identity is exact: the human-readable experiment ID identifies the study, while the package
SHA-256 identifies one immutable package revision.

Core does **not** contain a built-in experiment catalog, canonical research Contracts, pilot runtime
modules, or per-agent research apparatus directories.

## Ownership boundary

ACMM Core owns:

- Experiment Package schema and loader/stager;
- Experiment Contract schema/validation;
- public `acmm.experiment_api`;
- subject catalog and qualified subject bindings;
- subject adapters and evidence normalization;
- registered LLM definitions and LLM Rebasing;
- subject infrastructure/lifecycle managers;
- execution planning, persistence, retries, interruption handling, and Run Record validation;
- generic ACMM scoring, availability analysis, export, and reusable statistical machinery; and
- package/deployment provenance verification.

An Experiment Package owns:

- `experiment-package.yaml`;
- `experiment.yaml`;
- study README/protocol documentation;
- runtime fixtures/evaluators and other study-specific Python code;
- optional per-subject apparatus;
- optional package-local capability integrations through the generic integration API;
- experiment-specific analysis configuration/code where supported; and
- static package assets.

Adapters never belong to an Experiment Package. If adapters are externalized in the future, that is
a separate packaging problem.

## Repository model

The intended research workspace uses two source repositories plus mutable runtime state:

```text
~/acmm_repo/          ACMM Core
~/acmm-experiments/   Experiment Packages
~/acmm_runtime/       subject deployments and installed package snapshots
```

A package source is an ordinary local directory. ACMM does not require it to be a Python
distribution and does not install experiment code with `pip`.

The initial package shape is:

```text
<experiment-package>/
    experiment-package.yaml
    experiment.yaml
    README.md

    runtime/
        __init__.py
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

    integrations/
        <integration-id>/
            __init__.py
            ...

    analysis/
        ...

    assets/
        ...

    tests/
        ...
```

Only components declared by the package manifest are part of the package interface. A package may
legitimately omit runtime, apparatus, analysis, or assets when its scientific design does not need
them. In particular, a no-tool Minimal Agent Baseline does not need dummy apparatus merely to obtain
an experiment identity.

## Package manifest

`experiment-package.yaml` describes how Core consumes the package. `experiment.yaml` remains the
scientific/run Contract. Keeping these responsibilities separate prevents software-packaging details
from polluting the Contract.

The package manifest schema is owned by Core as standalone versioned JSON Schemas:

```text
src/acmm/experiment_package/schemas/experiment-package-v0.1.schema.json
src/acmm/experiment_package/schemas/experiment-package-v0.2.schema.json
```

Packages reference the supported schema version; they do not ship their own schema copies. Package
schema v0.2 adds ordered generic `integrations` components while preserving the v0.1 runtime,
apparatus, and analysis model. Existing v0.1 packages remain valid and unchanged.

The manifest binds the package to its Contract and optional runtime/apparatus/integration/analysis components.
Declared paths are package-relative and must remain valid when the source directory is moved.

## Package filesystem rules

Managed package content follows conservative filesystem rules:

- declared paths must resolve inside the package root;
- managed symlinks are rejected in v0.1;
- generated/VCS/editor/result artifacts are excluded from package identity;
- declared managed resources cannot live inside excluded directories; and
- package apparatus sources use component-relative paths rather than main-repository paths.

These rules make hashing, staging, and WSL/Windows portability deterministic.

## Validation, hashing, and installation

The researcher-facing source workflow is:

```text
acmm package validate <package-directory>
acmm package install <package-directory>
```

`validate` is non-executing. It loads the manifest, validates safe paths and the Contract, checks
cross-component identity, and computes deterministic component/package digests without importing
package runtime Python.

`install` performs the same validation and copies an immutable snapshot into the managed store:

```text
<ACMM_RUNTIME_ROOT>/_experiment_packages/<experiment-id>/<package-sha256>/
```

The default runtime root is `~/acmm_runtime`; `ACMM_RUNTIME_ROOT` may select another infrastructure
root. Research commands do not expose per-command runtime-root overrides.

Installed snapshots are digest-addressed. Reinstalling identical bytes converges on the same
revision. Multiple revisions of one experiment may coexist.

Selectors use:

```text
EXPERIMENT_ID
EXPERIMENT_ID@SHA_PREFIX
```

A plain ID is accepted only when it resolves unambiguously.

## Digests and provenance

ACMM computes an exact package SHA-256 plus component digests for the Contract, runtime, each declared
apparatus component, each declared integration component, and analysis configuration where present. Executions and deployments carry the
exact installed package identity rather than the original source path.

A package-backed deployment marker records enough information to reopen and verify the staged
snapshot without access to the source repository, including:

```text
experiment ID
package schema version
package SHA-256
Contract SHA-256
runtime SHA-256, when present
infrastructure subject
apparatus SHA-256, when present
```

A package may intentionally declare no apparatus for a subject. Such a deployment is valid when both
expected and observed apparatus identities are absent.

Passive reconstruction requires package provenance. Historical non-package experiment deployment
markers are unsupported; reconcile those subjects to a qualified baseline with
`acmm env set-llm <llm-id>`.

## Executable runtime boundary

Packages may contain trusted executable Python runtime code. The entrypoint is loaded only from a
verified staged package snapshot, never directly from an arbitrary mutable source tree.

Package runtime code depends on the narrow public interface:

```text
acmm.experiment_api
```

The API exposes experiment-facing runtime/context types, fixtures/evaluators, harness capabilities,
and normalized evidence helpers. It intentionally does not expose subject adapters or the internal
execution registry.

### Contract-directed interpreted evidence

For ordinary structured-output studies, packages should declare interpretation under Contract v0.2 `scenario.task.output_semantics` rather than reparsing raw evidence inside evaluators. `structured_object` binds a captured evidence channel such as `final_response` to a payload path and required payload keys. Core performs bounded syntax recovery once and exposes the result through `evaluation_context.interpreted_evidence`.

Persisted evidence remains immutable. Core records any syntax or semantic representation normalization in judgment detail when that evidence supports the judgment. Bounded syntax recovery covers common wrapper/presentation artifacts, including surrounding prose/Markdown, surplus trailing material, JSON-string wrapping, missing trailing delimiters, trailing commas, duplicated property-opening quotes, one structurally impossible unmatched closing delimiter, and up to three structural quote-escape layers. Required keys prevent fallback to an unrelated nested object.

When the external representation itself is scientifically observable, `structured_object.accepted_presentations` can declare the accepted form. `exact_json_object` means the captured evidence must already be one directly parseable JSON object after surrounding whitespace is ignored. Core still recovers bounded syntax noise for semantic task adjudication, but the interpreted view exposes `representation_status` and `representation_conforming` so a package can score output-interface Fidelity separately. Scalar semantic normalization is independent of this representation check.

Contract v0.2 may also declare scalar representation semantics under `scalar_fields`. For example, one integer field may explicitly accept both native JSON integer form and canonical numeric-string form. Core applies only those declared policies to the interpreted view; undeclared fields remain strict. Package evaluators then decide scientific correctness from the interpreted payload without knowing whether syntax repair or representation normalization occurred.

Low-level helpers such as `decode_json_object_text()` and `semantic_scalar_equal()` remain available through `acmm.experiment_api` for specialized cases, but they are no longer the preferred path for ordinary Contract-declared structured output.

## Apparatus boundary

Research apparatus is an experiment-specific delta layered onto a qualified subject baseline. It can
represent tools/plugins as well as deliberate agent-feature treatments such as enabling a native
memory capability.

Core resolves a declared staged apparatus component, verifies its digest, and passes its root to the
subject-specific apparatus compiler. The infrastructure layer never reconstructs research apparatus
from `repo_root + experiment_id`.

The package-aware compilers are:

- `with_hermes_apparatus()`;
- `with_open_webui_apparatus()`; and
- `with_agent_zero_apparatus()`.

Managed Tool/plugin sources retain relative paths plus an explicit apparatus `source_root`. Subject
managers resolve those bytes against the staged component, preserving path-containment checks and
package portability.

Subject `doctor()` uses the same package-aware apparatus identity during live readiness. This was
verified in EP-6 field testing after an early leak was found where live doctor checks still attempted
to hash a removed main-repository experiment directory.

## Generic capability integration boundary

Package schema v0.2 introduces ordered integration components for capabilities whose setup cannot be
expressed cleanly as the original static per-subject apparatus format. The Core abstraction is intentionally
tool- and transport-agnostic: Core understands that an integration component is staged, hashed, activated,
and composed into one or more subject profiles. It does not understand GitHub, MCP, databases, browsers,
or any other concrete tool technology.

An integration component declares:

```yaml
integrations:
  - id: example-capability
    api_version: "0.1"
    root: integrations/example-capability
    entrypoint: build_integration
    preset: controlled_v1
    configuration: {}
```

`preset` and `configuration` are opaque to Core. They are passed to trusted staged integration code through
the public `acmm.integration_api`. Raw credentials must not be embedded in the package; use the ACMM
secret-handling boundary for secret material.

The v0.1 integration API currently exposes a deliberately narrow reconciliation hook: an integration may
provide a subject-specific `compose_profile()` implementation. Composition occurs after any legacy
apparatus overlay and before the normal subject manager is constructed. Manifest declaration order is the
composition order. The integration must preserve the subject profile type, so normal manager validation,
apply, status, doctor, and teardown behavior remains authoritative.

This boundary follows three ownership rules:

- **Core owns lifecycle and provenance.** It loads only immutable staged integration code, verifies component
  identity, controls deterministic composition order, and invokes the existing subject managers.
- **The integration owns mechanics.** Concrete connection mechanisms, host-specific profile changes, and
  external capability setup belong to integration code, not tool-specific branches in Core.
- **The experiment owns scientific meaning.** Tool surface, scenario state, requirements, prompts, and
  adjudication remain package concerns.

The first implementation is package-local by design. If a concrete integration becomes reusable across
experiments, the same public interface can later be satisfied by a shared integration distribution/repository
without changing the package-to-Core lifecycle contract. Shared integration discovery is intentionally
deferred until actual reuse establishes the necessary dependency and versioning semantics.

The integration API is not synonymous with MCP. MCP may be implemented by one integration provider, just
as a future integration may use an HTTP API, local service, database driver, or another mechanism. Core must
not add mechanism-specific branches merely because one experiment happens to use that mechanism.

## LLM and subject boundary

Packages bind qualified subject IDs in their Contract. Core resolves those subject identities to
agent infrastructure/adapters and registered LLM definitions. Package runtime code does not select
provider URLs, instantiate models, or implement LLM Rebasing.

This keeps the research focus on agent-side treatments while allowing the qualified model baseline to
be changed/revalidated centrally. A frozen package may bind a historical registered LLM; changing
that binding creates a different package revision/study configuration rather than silently rebasing
the old artifact.

## CLI lifecycle

### Package management

```text
acmm package validate <source>
acmm package install <source>
acmm package list
acmm package show <selector>
acmm package apply <selector>
acmm package detach <selector>
acmm package uninstall <selector>
acmm package uninstall <selector> --detach-if-applied
```

`apply` prepares the full package cohort. `detach` affects only subjects carrying that exact package
revision and restores each to the LLM binding declared by the package. Uninstall refuses to delete an
attached revision unless `--detach-if-applied` explicitly grants permission to detach first.

### Execution

```text
acmm run <selector> [execution selectors/options]
```

`run` accepts only an installed package selector. It does not accept raw Contracts, package source
paths, or infer the experiment from deployment state.

Strict mode is the default. Package-deployment preflight must match exactly before acquisition.
`--apply-if-needed` grants permission to reconcile only subjects required by the selected run set.
After reconciliation ACMM re-reads persisted state and repeats the strict deployment preflight; a
successful apply call alone is never proof of readiness.

`--preview` is non-mutating and reports the selected run plan plus deployment divergence. If context
is divergent, preview states whether strict mode would fail or whether `--apply-if-needed` would
permit reconciliation.

`--start-if-needed` is a separate narrow permission to start a correctly deployed but stopped
runtime. It does not repair package/apparatus mismatch.

### Subject environment

Experiment selection does not belong to `acmm env`:

```text
acmm env set-llm <llm-id>
acmm env status
acmm env doctor --start-if-needed
acmm env stop
```

The internal `acmm-infra <subject> ...` surface is baseline/engineering only. It does not select
research experiments. Status/doctor may observe package provenance because that is required for
readiness diagnostics.

## Execution provenance

Completed `experiment_execution.yaml` manifests preserve exact package provenance and record whether
permissioned deployment reconciliation was relevant/performed. The manifest remains YAML for human
inspection and is structurally described by:

```text
src/acmm/runtime/schemas/experiment-execution-v0.1.schema.json
```

Operational diagnostics such as captured container logs and provider-usage snapshots remain outside
ACMM compliance scoring. See `docs/runtime/provider_usage.md` and `docs/security/secrets.md`.

## Analysis ownership

Generic analysis remains Core-owned: normalized export, availability, requirement/dimension
estimation, uncertainty, and GOBPFR aggregation. Experiment-specific designs belong with the package.

Core retains reusable statistical machinery, including interaction-analysis routines, without
registering experiment IDs or inferring a design from scenario names. A package that needs an
interaction analysis should provide an explicit scientifically justified design/configuration.

## Credentials and external resources

Secrets are never package contents. A package may describe that an external capability is required,
but credentials stay in local/runtime secret mechanisms. For Ollama Cloud usage telemetry, for
example, `OLLAMA_API_KEY` should be stored outside both source repositories.

External resources must be handled reproducibly enough that execution provenance can explain what was
used. Package hashing cannot make a live remote service immutable, so resource identity/version and
availability should be captured explicitly when such dependencies become part of a study.

## Current externalization state

The package architecture was field-validated by externalizing
`controlled_authoritative_state_interference_v1` into the separate experiment-package repository.
Core does not own its Contract, runtime implementation, apparatus, or experiment-specific tests.
The external package remains temporarily available as a known debugging package until the new
Minimal Agent Baseline is operational.

Live WSL field testing validated package source installation, digest-addressed resolution, preview,
permissioned reconciliation, the required second strict preflight, package-aware live readiness for
Hermes/Open WebUI/Agent Zero, execution provenance, runtime-log capture, detachment/uninstall safety,
and optional Ollama usage telemetry.

## Package-only Core checkpoint

The legacy built-in experiment architecture has been removed from Core. Generic Core regression tests
use neutral synthetic Contract/runtime/apparatus fixtures rather than historical research studies.
Research experiment code and apparatus must enter through the Experiment Package boundary.

This is the baseline for the next substantive study series:

1. Minimal Agent Baseline;
2. realistic real-tool control study; and
3. isolated agent-side feature interventions against the relevant tool-only control.
