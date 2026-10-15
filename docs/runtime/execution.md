# Execution Orchestration

## Purpose

Execution orchestration transforms one deterministic `planned_run` into one realized
execution result.

The current runtime boundary is:

```text
validated Experiment Contract
        |
        v
experiment_plan
        |
        v
planned_run
        |
        v
execution registry
        |
        +-- subject adapter
        +-- environment fixture
        +-- opportunity evaluators
        |
        v
execute_planned_run()
        |
        v
executed_run
        |
        +-- Run Record
        +-- captured evidence artifacts
```

The implementation is intentionally platform-neutral. Hermes Agent, Open WebUI, Agent
Zero, and other concrete subjects will be integrated later through subject adapters.

## Runtime interfaces

### Subject adapter

A subject adapter executes one Contract subject against one scenario and returns:

- the observed termination reason;
- normalized evidence artifacts;
- realized subject provenance;
- optional infrastructure failure information.

The adapter receives the Contract termination policy but does not construct Run Records.

### Environment fixture

An environment fixture prepares the scenario environment according to the Contract reset
policy.

It returns:

- an opaque environment handle consumed by the subject adapter;
- realized environment provenance.

A scenario without a fixture receives a `None` runtime handle and a minimal environment
provenance object.

### Opportunity evaluator

An evaluator receives the subject, scenario, one opportunity, the referenced check, and the
captured evidence.

It returns one atomic judgment:

```text
outcome
evidence_ids
optional detail
```

The orchestrator evaluates opportunities in scenario declaration order.

## Execution registry

`execution_registry` is runtime dependency injection rather than part of the scientific
Experiment Contract.

It maps:

```text
subject_id     -> subject adapter
fixture_id     -> environment fixture
evaluator_id   -> evaluator implementation
```

This preserves the Contract boundary: scientific definitions identify subjects, fixtures,
and evaluators symbolically while runtime configuration supplies executable
implementations.

## Evidence

Subject adapters return normalized `captured_evidence` objects containing:

```text
id
channel
artifact
content bytes
```

The orchestrator computes SHA-256 digests from the captured bytes and places only the
manifest data in the Run Record.

The artifact bytes remain alongside the Run Record in the returned `executed_run` bundle.
Persistent/atomic writing to a result directory is intentionally deferred to the next
runtime layer.

## Valid and invalid executions

An ordinary agent outcome such as completion, timeout, or maximum-step termination may
still be scientifically valid.

For example:

```text
termination = timeout
validity    = valid
```

is permitted.

Infrastructure failures produce:

```text
validity = invalid
failure  = diagnostic information
```

A subject-adapter failure may preserve partial captured evidence.

If an evaluator fails after subject interaction completed, the Run Record preserves the
observed interaction termination while marking the execution invalid. For example:

```text
termination = agent_completed
validity    = invalid
failure     = orchestration_error
```

This avoids incorrectly claiming that the interaction itself ended because of a harness
error.

## Partial provenance on invalid runs

Infrastructure failure may occur before subject or environment state is realized.

Therefore Run Record v0.1 now requires:

```text
all runs:
    harness provenance

valid runs:
    harness + subject + environment provenance

invalid runs:
    harness provenance, with subject/environment provenance when available
```

This prevents the runtime from fabricating realized provenance for executions that never
reached those stages.

## Error boundary

`execute_planned_run()` converts runtime exceptions into a valid Run Record describing an
invalid execution rather than allowing an orchestration exception to masquerade as agent
non-compliance.

The current implementation deliberately does not:

- persist result directories;
- retry failed planned runs;
- select a replacement attempt;
- execute a complete experiment plan;
- provide concrete Hermes/Open WebUI/Agent Zero adapters;
- expose an `acmm run` command.

Those belong to subsequent runtime layers.
