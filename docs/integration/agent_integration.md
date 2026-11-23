# Agent Integration

ACMM subject integrations keep experiment-independent host management separate from Experiment Package scientific capability configuration.

## Principle

Core should not acquire experiment-specific branches for a concrete tool, service, or study. The stable ownership rule is:

> Core owns lifecycle and provenance. Subject infrastructure owns host mechanics. Experiment Packages own scientific capability and treatment configuration.

Subject infrastructure lives under `src/acmm_infra/subjects/<subject>/` with experiment-independent profiles under `infrastructure/<subject>/`.

## Adding a subject

A supported subject integration should provide:

1. a pinned agent distribution/image and externally visible version;
2. an experiment-independent baseline profile;
3. a host API/client wrapper for supported application operations;
4. a manager with deterministic `init/apply/up/down/status/doctor` behavior;
5. external payload files for executable/configuration documents that are sent into the managed application;
6. a runtime adapter that creates a fresh run context and captures normalized evidence;
7. subject catalog registration; and
8. regression coverage for lifecycle, evidence, failure, and cleanup behavior.

Research capability configuration should enter through Experiment Package integrations rather than a Core special case.

## Manager responsibilities

A manager owns desired state and reconciliation for one host. `apply` should be deterministic and bounded; `status` observes without mutating; `doctor` verifies live readiness; and `down` should be non-destructive unless a separate explicit reset operation says otherwise.

Package-aware construction composes the qualified baseline with package integration requests while preserving the normal deployment root and host identity.

## Payload rule

Programs or configuration documents sent into a managed application should live under that subject's payload tree rather than being embedded as large source strings inside manager/client code. Ordinary command arguments and serialized runtime data do not need separate payload files merely because they use JSON or another encoding.

## Runtime adapter responsibilities

Adapters remain experiment-independent. They own fresh per-run context creation, host invocation, supported timeout/termination handling, cleanup, normalized conversation/tool-call/tool-result/final-response evidence, and relevant subject provenance.

Adapters must not configure one experiment's external capability state. Package integrations and package runtime fixtures own that responsibility.

## Qualification requirements

Before a subject/integration combination is used for research, establish at minimum:

- the exact pinned agent and LLM context;
- baseline isolation settings;
- expected capability configuration and absence of unintended capability exposure;
- live host readiness;
- exact package/integration provenance when attached;
- clean detach/baseline restoration; and
- evidence capture for success and important failure/recovery paths.

These are reproducibility requirements rather than a project-management checklist.
