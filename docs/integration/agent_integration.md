# Agent Integration Workflow

This note describes how a new agent host is integrated into ACMM Core. It intentionally separates
subject integration from research capability integration.

## Principle

A qualified subject is an agent system plus its reproducible baseline host configuration. A research
experiment may later add package-owned integrations, but those additions must not be baked into the
subject baseline merely because one experiment needs them.

The implementation boundary is:

```text
src/acmm_infra/subjects/<subject>/
    api.py or client.py
    manager.py
    profile.py
    cli.py
    payloads/
```

Cross-subject orchestration belongs in `src/acmm_infra/subjects/factory.py`, cohort/lifecycle modules,
and the public Core CLI.

## Integration steps

1. Pin the agent distribution/image and record its externally visible version.
2. Define an experiment-independent profile under `infrastructure/<subject>/profile.yaml`.
3. Implement the host API/client wrapper used for supported application operations.
4. Implement a manager with `init/apply/up/down/status/doctor` semantics.
5. Keep executable/configuration payloads in the subject `payloads/` directory rather than inline in
   Python source.
6. Add the runtime adapter that executes one Contract run and captures normalized evidence.
7. Register qualified subject IDs in the subject catalog.
8. Add baseline, lifecycle, adapter, evidence, and failure-path tests.
9. Exercise cold-start and LLM qualification before research use.

## Manager responsibilities

A manager owns desired state and reconciliation for its host. `apply` should be deterministic and
bounded; `status` observes without mutating; `doctor` verifies live readiness; `down` is
non-destructive unless a separate explicit reset operation says otherwise.

Managers should expose generic managed-configuration surfaces where package integrations need to
request host state. Do not add `if tool == ...` branches to Core or subject managers. Concrete
service semantics belong to the integration implementation.

Package-aware manager construction is:

```text
qualified profile
    -> integration profile composition
    -> normal manager
    -> integration manager composition
```

An integration wrapper must preserve the normal subject deployment root and Docker client identity.
That prevents package code from silently redirecting ACMM to an unrelated runtime.

## Payload rule

Do not embed programs/configuration documents inside manager/client source when they are logically
payloads sent to the managed application. This includes Python, shell, JSON, YAML, or other
languages. Store them under the subject's `payloads/` tree and load/render them explicitly.

Small ordinary command arguments and serialized runtime data are not payload source files merely
because `json.dumps()` is used; the rule targets embedded source/configuration artifacts.

## Runtime adapter responsibilities

Adapters should remain experiment-independent. They own:

- fresh per-run conversation/context creation;
- invocation of the qualified subject interface;
- timeout/termination handling the host can actually enforce;
- cleanup;
- normalized conversation/tool-call/tool-result/final-response evidence; and
- subject provenance relevant to interpretation.

Adapters must not configure one experiment's tool state. Package integrations and fixtures own
experiment-specific capability/state setup.

## Real capability integrations

A real external capability should normally enter through an Experiment Package integration. The
integration may use each host's native mechanism differently—those differences are part of the agent
system—but the scientific capability can still be held constant upstream.

Core should understand only the generic integration lifecycle/provenance interface. Tool-specific
code can initially live in the experiment package. If reuse later justifies a shared integration
repository/distribution, it should implement the same Core interface rather than adding a new Core
special case.

## Verification checklist

Before qualifying an agent/integration combination, verify:

- exact pinned agent/version;
- model/provider/context state;
- baseline isolation settings;
- expected capability configuration;
- no unexpected capability exposure;
- live host readiness;
- exact package/integration provenance when a package is attached;
- clean detach/baseline restoration; and
- evidence capture for success, rejection, timeout, and recovery paths.
