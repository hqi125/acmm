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

The orchestration layer is platform-neutral. Hermes Agent, Open WebUI, and Agent Zero are
provided through concrete subject adapters; additional subjects can use the same adapter boundary.

## Runtime interfaces

### Subject adapter

A subject adapter executes one Contract subject against one scenario and returns:

- the observed termination reason;
- normalized evidence artifacts;
- realized subject provenance;
- optional infrastructure failure information.

The adapter receives the Contract termination policy but does not construct Run Records.

For `timeout_seconds`, the Contract value is the subject execution budget. Harness work required
after the bound fires--for example partial-evidence capture, session export, or cleanup--may finish
after that budget without extending the subject's permitted execution time.

### Environment fixture

An environment fixture prepares the scenario environment according to the Contract reset
policy.

It returns:

- an opaque environment handle consumed by the subject adapter;
- realized environment provenance;
- optional Contract-declared initial-state evidence;
- optional non-secret task bindings used to materialize runtime values into the scenario prompt.

A fixture may additionally implement `observe()` and `finalize()`. `observe()` captures trusted
post-execution environment evidence before adjudication. `finalize()` restores external state before
judgments are evaluated, so evaluator failure cannot leave the next run contaminated. A scenario
without a fixture receives a `None` runtime handle and a minimal environment provenance object.

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

Subject adapters and trusted environment fixtures return normalized `captured_evidence` objects containing:

```text
id
channel
artifact
content bytes
```

The orchestrator computes SHA-256 digests from the captured bytes and places only the
manifest data in the Run Record.

The artifact bytes remain alongside the Run Record in the returned `executed_run` bundle.
Persistent/atomic writing to a result directory is owned by the persistence layer rather than
`execute_planned_run()` itself.

## Valid and invalid executions

Core treats validity as scientific evaluability, not task success. The default assumption is
that a preserved run is evaluable; invalidity is exceptional and requires a failure that
undermines the observation/provenance boundary itself. Agent mistakes, rejected tool calls,
and recoverable external difficulties remain evidence when they can be observed faithfully.
This is the Evaluability Boundary Principle.

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

A subject-adapter failure may preserve partial captured evidence. When the adapter
exhausts retries against its model/provider, the canonical termination is
`provider_error`; this termination is always invalid and therefore never contributes
agent compliance evidence.

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

`execute_planned_run()` deliberately does not own result-directory persistence, retry policy,
selected-attempt resolution, multi-run session orchestration, subject deployment, or CLI behavior.
Those responsibilities are implemented by the surrounding persistence, experiment-execution,
subject-adapter, and CLI layers.
