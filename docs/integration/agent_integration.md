# General Agent Integration Workflow

## Purpose

Integrating an agent into ACMM is not only a matter of making a prompt reach a model and
receiving a response. A subject becomes useful for comparative experiments only after ACMM can
reproduce its deployment, constrain its experimental surface, execute it through a stable machine
interface, capture trustworthy evidence, enforce the Contract controls that the subject claims to
support, and explain important framework-side behavior that could otherwise be mistaken for agent
behavior.

The distinction is important:

```text
agent can execute a task
        !=
agent is integrated
        !=
agent is qualified as an ACMM experimental subject
```

The first statement may require only a working installation. The second requires substantial
adapter and infrastructure code. The third additionally requires evidence that the configured
baseline is reproducible, isolated enough for the intended experiment, and not dominated by an
accidental integration artifact.

This document describes the general workflow ACMM should follow when adding a new agent. It is
intentionally agent-neutral. Concrete products will require different APIs, deployment mechanisms,
prompt/profile controls, evidence sources, and cleanup procedures, so the corresponding
agent-specific infrastructure and runtime notes remain authoritative for implementation details.

## Integration layers

A useful way to reason about an integration is as a stack of related but separate layers:

```text
Experiment Contract
        |
        v
qualified subject binding
        |
        v
subject adapter
        |
        +-- runtime/API client
        +-- evidence normalization
        +-- termination mapping
        |
        v
managed agent baseline
        |
        +-- deployment
        +-- model/provider route
        +-- profile/prompts
        +-- tools/plugins
        +-- isolation controls
        |
        v
agent framework + model provider
```

A failure in any layer can contaminate the observed result. For example, a tool-use failure may be
real agent behavior, but it can also originate from malformed tool registration, an unexpected
prompt bootstrap turn, a framework retry, a hidden background model call, or an evidence parser
that failed to recognize a valid native tool call.

For that reason, integration work should progress from lower-level reproducibility toward
experiment-level qualification rather than beginning with a large comparative run.

## Phase 1: define the subject before writing the adapter

The first task is to decide exactly what ACMM means by the new subject. The subject should be a
qualified configuration, not merely a product name.

At minimum, freeze or explicitly record:

- agent/framework version and, when practical, immutable image or package identity;
- execution platform and deployment mode;
- model name and provider route;
- model context length and material inference settings;
- relevant agent profile or mode;
- enabled tool surface;
- memory, background processing, review, naming, search, and other autonomous features;
- termination controls that can be enforced exactly;
- any auxiliary or utility model roles used by the framework.

The goal is not to make every agent internally identical. Different orchestration strategies are
part of what ACMM may eventually compare. The goal is to remove accidental configuration
ambiguity so a later result can be attributed to a known subject baseline.

A subject should not be called equivalent to another subject merely because both use the same
underlying LLM. Framework prompts, conversation bootstrapping, tool routing, retries, memory,
parallel execution, utility models, and final-response handling can all materially change behavior.

## Phase 2: study the agent's real machine interface

Before implementing ACMM code, inspect what the pinned agent version actually supports. Do not
assume documentation for a newer release applies to the qualified version.

Prefer, in roughly this order:

1. a documented programmatic API;
2. a documented non-interactive CLI;
3. persisted session/event data exposed by the product;
4. narrowly scoped framework files or logs needed for evidence capture;
5. UI automation only as a last resort.

The integration study should answer questions such as:

- How is a fresh conversation/context/session created?
- How is the experiment prompt submitted?
- How are tools registered or enabled?
- How is per-run fixture state supplied to those tools?
- Does the request return only after the agent is actually finished?
- Where are tool calls and tool results persisted?
- Can partial state be obtained after a timeout?
- How is a conversation terminated and deleted?
- What authentication, CSRF, cookie, token, or lifecycle state is required?
- Does the framework perform hidden model calls outside the visible task loop?
- Does the framework retry model requests automatically?
- Can multiple tool calls appear in one model turn?
- Does it have synthetic bootstrap messages before the real task?
- Can it enforce a run timeout internally, or only through an outer request timeout?
- Does it expose an exact maximum-turn/step control?

This investigation often determines the architecture of the integration. A clean HTTP API may
produce an API client plus a small adapter. A CLI subject may need subprocess management and a
separate persisted-session parser. A framework with mutable profile state may require a substantial
infrastructure reconciler before the runtime adapter is trustworthy.

## Phase 3: build repository-managed desired state

A comparative subject should not depend on undocumented manual setup. ACMM should be able to
reconstruct and inspect the qualified baseline from version-controlled desired state.

A typical source layout is conceptually:

```text
infrastructure/<agent>/
|-- profile.yaml
|-- defaults/
|-- README.md
`-- experiments/
    `-- <experiment-id>/
        |-- apparatus.yaml
        `-- tools/ or plugins/
```

The exact files vary by agent, but the managed baseline generally needs to cover:

- deployment image/package and version;
- ports, volumes, environment variables, and provider endpoints;
- model configuration;
- profile or prompt overrides that belong to the qualified subject baseline;
- experiment-isolation settings that remain invariant across experiments;
- mutable application settings that must be reconciled through supported APIs;
- expected runtime paths and ownership boundaries.

Experiment-facing tools, plugins, permissions, and other laboratory apparatus should begin
under `experiments/<experiment-id>/` even when that duplicates small amounts of code. Promote
a component into the subject baseline only after it is genuinely experiment-independent.

Secrets should not be committed as desired-state content. The repository may describe where a
credential comes from, but credentials should stay in the appropriate local/runtime mechanism.

### Infrastructure lifecycle

Where practical, a new managed subject should fit the common lifecycle:

```text
acmm-infra <agent> init
acmm-infra <agent> apply
acmm-infra <agent> apply --experiment <experiment-id>
acmm-infra <agent> up
acmm-infra <agent> status
acmm-infra <agent> doctor
acmm-infra <agent> down
```

This is a command vocabulary, not a universal ordering constraint. The preferred full
experiment-aware `apply` may use different subject-specific provisioning mechanics. The
narrower `apply-tools --experiment <experiment-id>` path may additionally require a running
service, as Open WebUI does. The subject-specific README owns the exact sequence.

The commands do not need identical internal implementations. Their semantics should be consistent:

- `init` creates the mutable runtime/deployment skeleton without silently qualifying it;
- `apply` without `--experiment` reconciles the experiment-independent qualified baseline and
  removes active experiment apparatus;
- `apply --experiment <experiment-id>` composes the baseline and selected experiment
  apparatus as one requested target state;
- `apply-tools --experiment <experiment-id>` reconciles only the selected experiment-facing
  Tool/plugin/policy layer;
- `apply-experiment <experiment-id>` remains a compatibility alias for the full
  experiment-aware `apply`;
- `up` starts the qualified service and waits for meaningful readiness;
- `status` reports observed state without claiming qualification;
- `doctor` discovers the deployment context from the apparatus marker and verifies either the
  baseline alone or the composed baseline plus active experiment apparatus;
- `down` stops the service without destroying the version-controlled definition.

For a new product, this usually requires agent-specific code under `src/acmm_infra/`: a manager,
API/process/Docker client as appropriate, root-CLI wiring, profile parsing, and tests.

### Doctor is a qualification gate, not a ping test

A health endpoint that returns HTTP 200 is not enough for ACMM. `doctor` should verify the state
that matters scientifically, such as:

- expected version/image identity;
- provider/base URL;
- configured experiment model;
- context length and important inference settings;
- required profile selection;
- exact managed tool/plugin content when feasible;
- disabled background or memory features that are part of the baseline;
- availability of the configured model route;
- other agent-specific settings whose drift would change experiment behavior.

If a setting materially affects the subject definition, `doctor` should ideally detect its drift.

## Phase 4: qualify the experiment-isolation baseline

Isolation does not mean removing every distinctive feature from the agent. It means identifying and
controlling framework activity that would otherwise introduce unrelated state, hidden requests, or
cross-run dependence.

Agent-side features that deserve explicit attention include:

| Area | Questions to answer |
| --- | --- |
| Conversation bootstrap | Are synthetic user/assistant messages inserted before the real task? Can the model mistake them for active instructions? |
| Memory | Is previous conversation/task state recalled, written, consolidated, or shared across runs? |
| Background model calls | Are titles, summaries, reviews, routing decisions, reflections, or other auxiliary outputs generated automatically? |
| Tool discovery/routing | Are controlled tools exposed directly, wrapped in search/router tools, or dynamically discovered? |
| Retries | Can the framework silently repeat provider calls or tool operations? |
| Utility models | Are separate model roles invoked, and are they part of the intended subject baseline? |
| Projects/workspaces | Can inherited project state or files leak into a fresh experimental run? |
| Network/tool surface | Can the agent access unrelated built-in tools, web search, MCP/A2A services, skills, or plugins? |
| Auto-updates | Can the framework change itself or its assets during a study? |
| Finalization | Does the framework perform extra reasoning or model calls after the task is already solved? |

A narrow compatibility override is acceptable when required to remove an accidental mismatch
between a framework convention and the chosen model, but it needs strong justification. The safest
pattern is:

```text
observe anomaly
    -> collect framework evidence/logs
    -> identify a specific integration artifact
    -> change one baseline variable
    -> rerun the same stress population
    -> compare before/after behavior
```

Do not immediately normalize every unusual behavior. Slow reasoning, redundant tool use, or a
strange orchestration strategy may genuinely belong to the agent. The integration should intervene
only when there is evidence that the behavior comes from an unintended configuration or
compatibility artifact rather than the subject architecture being studied.

## Phase 5: implement the machine client separately from ACMM semantics

Keep protocol mechanics out of the subject adapter when possible.

A transport/client module typically owns details such as:

- HTTP requests and response decoding;
- authentication and CSRF/cookie state;
- subprocess or Docker command invocation;
- session/context creation and deletion;
- raw log/session retrieval;
- API error mapping;
- request-level timeout behavior.

Conceptually:

```text
src/acmm/adapters/<agent>_api.py
src/acmm/adapters/<agent>_session.py
```

or another agent-appropriate split.

The client should expose operations meaningful to the adapter while remaining ignorant of ACMM
requirements, dimensions, and judgments. This separation makes protocol behavior testable without
running an experiment and prevents scientific semantics from becoming tangled with product APIs.

## Phase 6: implement the subject adapter

The ACMM runtime boundary is the `subject_adapter` protocol:

```text
execute(subject, scenario, environment, termination)
    -> adapter_execution_result
```

A concrete adapter is responsible for turning one planned task into normalized evidence and
termination/provenance information. It does not decide compliance.

The adapter generally needs to:

1. validate the Contract values it relies on;
2. create fresh per-run agent state;
3. apply fixture-controlled tool state without changing the scientific task definition;
4. submit the scenario prompt through the qualified machine interface;
5. enforce supported termination controls;
6. capture complete or partial evidence;
7. normalize that evidence into ACMM channels;
8. verify realized model/subject provenance when the agent exposes it;
9. clean up per-run state;
10. return an `adapter_execution_result`.

The adapter should not contain opportunity scoring logic. The experiment evaluator judges normalized
evidence later.

### Environment and fixture boundary

The environment fixture owns scenario-specific controlled state. The subject adapter should consume
the prepared environment through a generic binding suitable for that agent rather than embedding a
specific experiment's magic values or requirement semantics.

For example, the architecture should look like:

```text
scenario fixture
    -> prepared environment
    -> agent-specific runtime representation
    -> controlled tool
```

not:

```text
adapter hard-codes Experiment 1 values
```

This keeps the same adapter reusable across future experiments.

## Phase 7: normalize evidence without hiding agent behavior

The adapter should project agent-specific traces into the evidence channels requested by the
Contract. Common channels include:

```text
conversation
tool_calls
tool_results
final_response
```

Normalization exists to make evaluators platform-neutral, not to make all agents look behaviorally
identical.

Preserve information needed to explain what happened, such as:

- native tool name;
- structured arguments;
- call/result identifiers;
- success/error state;
- timestamps when available;
- final assistant content;
- transport/wrapper tool names when a framework routes through an intermediate mechanism;
- raw framework/session identifiers useful for provenance.

If a framework wraps a controlled tool call inside a router/search bridge, ACMM may expose the real
underlying controlled call for evaluation while retaining the wrapper identity as transport
metadata. What it should not do is silently delete redundant calls, reorder events to make the
agent look correct, fabricate missing calls, or turn textual pseudo-tool syntax into a successful
native call.

Evidence normalization should answer this question:

> What did the agent/framework actually do, expressed in a stable ACMM vocabulary?

It should not answer:

> What would make this run easiest to score?

## Phase 8: define termination semantics precisely

Termination is one of the easiest places for integrations to become unfair.

For each subject binding, explicitly declare whether the qualified interface can enforce:

```text
max_steps
timeout_seconds
```

Do not emulate a Contract control loosely and claim equivalence. If the pinned subject has no exact
per-run maximum-step mechanism, the binding should report that limitation and `acmm run` should
reject a Contract requiring it.

For timeouts, distinguish:

```text
subject execution budget
        vs
harness cleanup/evidence-capture grace
```

The agent must not receive extra task-solving time merely because ACMM needs a few additional
seconds to retrieve partial logs or delete a context after the timeout fires.

A timeout can still be a scientifically valid agent outcome when the adapter captured enough
trustworthy evidence. Failure to launch the framework, authenticate, parse evidence, or verify
critical provenance is generally a harness/infrastructure failure instead.

## Phase 9: register the qualified subject

Only after the adapter and baseline are coherent should the subject be exposed through the
experiment-facing catalog.

The current registration point is conceptually:

```text
src/acmm/subjects/catalog.py
```

A `subject_execution_binding` connects the Contract `subject_id` to:

- an adapter builder;
- a researcher-readable runtime description;
- exact supported termination controls.

This catalog is intentionally a qualification boundary. An agent installed on the machine should
not automatically become runnable by `acmm run`.

## Phase 10: test in layers

Live cloud-backed runs are valuable, but they should be the last part of the testing pyramid, not
the first.

### Protocol/client tests

Test API and process behavior with controlled fixtures/mocks:

- request shapes;
- authentication handling;
- response parsing;
- error/status mapping;
- timeout propagation;
- session/log decoding.

### Infrastructure tests

Cover:

- profile/default parsing;
- deployment generation;
- baseline `apply`, experiment-aware `apply`, and Tool-only experiment reconciliation;
- `status` observations;
- `doctor` drift detection;
- command failure and timeout paths;
- line-ending/repository conventions.

### Adapter tests

Exercise:

- successful tool path;
- tool failure path;
- retrieval/prerequisite failure path;
- timeout with partial evidence;
- malformed or incomplete agent responses;
- model/provenance mismatch;
- cleanup after both success and failure;
- agent-specific wrapper/transport normalization.

### Experiment integration tests

Verify that the qualified subject can be constructed by the same registry/catalog path used by
`acmm run`, and that Contract termination/tool/evidence requirements are compatible before live
acquisition begins.

### Live smoke test

A smoke should be intentionally small and outside the ordinary unit-test suite when it consumes a
real cloud model. It should prove the complete path:

```text
managed baseline
    -> real agent
    -> controlled tool
    -> normalized evidence
    -> expected final task result
```

A smoke pass qualifies plumbing. It does not yet qualify behavioral stability.

## Phase 11: stress the subject before a canonical experiment

After smoke qualification, run a small but complete subject-only stress population covering the
experiment's success and controlled-failure scenarios.

For example, `acmm run` now supports subject filtering, run-index ranges, and preview specifically
so this can be done without hand-building long index lists:

```text
acmm run experiment.yaml --subject <subject_id> --preview
acmm run experiment.yaml --subject <subject_id>
```

During stress qualification, inspect more than pass/fail counts. Useful diagnostics include:

- per-run latency and latency spread;
- timeout frequency;
- tool-call order;
- redundant/restarted work;
- final-response completeness;
- malformed native-tool calls or textual tool-call leakage;
- model/provider request counts when observable;
- framework logs around slow or anomalous runs;
- cleanup success and cross-run isolation.

Request counts are particularly useful as an operational diagnostic because they can reveal hidden
framework turns, batching, retries, title generation, or repair loops. They are not themselves an
ACMM compliance outcome and should not be assumed to equal `tool_calls + 1` across different agent
architectures.

## Phase 12: investigate outliers before declaring them agent behavior

A comparative study should preserve genuine agent differences, but it should not casually label an
extreme outlier as intrinsic behavior when an integration artifact remains plausible.

Escalate to framework logs/configuration inspection when an agent shows symptoms such as:

- order-of-magnitude latency differences on trivial tasks;
- repeated timeouts where peers are stable;
- repeated successful work followed by task restart;
- confusion about which user message is active;
- unexpectedly high model-request counts;
- unexplained tool retries;
- evidence that background features are still active;
- behavior that changes dramatically between otherwise identical batches.

The goal of investigation is not to make the agent score better. It is to decide whether the
observed behavior belongs to the qualified subject or to an accidental setup mismatch.

A convincing correction should normally satisfy all of the following:

- the artifact is observable in agent/framework evidence;
- the correction is narrow and explainable;
- task requirements are unchanged;
- the model/provider is unchanged unless that is the variable being studied;
- the same stress population is rerun;
- before/after timing and behavior can be compared;
- the correction becomes version-controlled desired state and `doctor` coverage when practical.

## Phase 13: freeze the baseline before canonical acquisition

Once the integration survives smoke and stress testing, freeze the baseline that will be used for
the canonical experiment.

Before acquisition, record or verify:

- repository revision;
- Contract SHA-256;
- agent/framework version or image digest;
- managed profile state;
- exact model/provider route;
- ACMM tool version/state;
- termination compatibility;
- experiment-isolation settings;
- `doctor` success.

After this point, a baseline-affecting integration change should normally invalidate comparability
with earlier canonical runs unless the research design explicitly treats the change as a new
subject/configuration.

Engineering stress runs remain useful, but they should not be silently mixed with the final
comparative dataset if their baseline differs from the frozen configuration.

## Phase 14: preserve interruption and failure integrity

Integration code should assume that processes, APIs, networks, and users can interrupt execution.

For ordinary run failures, preserve whatever evidence is trustworthy and distinguish agent outcome
from harness failure. For experiment-level Ctrl+C interruption, ACMM preserves already-persisted Run
Records but does not publish a normal completion manifest. The partial session is auditable but not
exportable as if it were a completed experiment.

Do not add agent-specific resume behavior to repair an interrupted canonical execution. Restarting a
new execution is simpler scientifically because it avoids silently combining observations obtained
across different temporal/runtime conditions.

## Typical coding surface for a new agent

A substantial integration commonly touches most of the following areas. Not every agent needs every
file, but an integration that changes only one adapter file should prompt a deliberate review of
its infrastructure, isolation, evidence, and qualification requirements.

| Area | Typical responsibility |
| --- | --- |
| `infrastructure/<agent>/profile.yaml` | Qualified expected baseline and version/model settings |
| `infrastructure/<agent>/defaults/` | Deployment/runtime defaults |
| `infrastructure/<agent>/tools/` or `plugins/` | Controlled ACMM tool implementation |
| `infrastructure/<agent>/README.md` | Product-specific desired-state and lifecycle notes |
| `src/acmm_infra/<agent>.py` | Infrastructure manager/reconciler |
| `src/acmm_infra/<agent>_api.py` or client module | Product API/process mechanics used for provisioning |
| `src/acmm_infra/cli.py` | `acmm-infra` command registration/presentation |
| `src/acmm/adapters/<agent>.py` | ACMM subject adapter and evidence projection |
| `src/acmm/adapters/<agent>_api.py` / session parser | Runtime machine interface and raw-session decoding |
| `src/acmm/subjects/catalog.py` | Qualified execution binding and termination capability |
| `docs/runtime/<agent>_adapter.md` | Runtime/evidence/termination semantics |
| `tests/infrastructure/` | Desired-state, manager, client, and drift tests |
| `tests/adapters/` | Adapter, timeout, evidence, parser, and cleanup tests |
| `tools/smoke_<agent>.py` | Optional live qualification smoke |

The coding effort is often dominated by product-specific state management rather than the final
`subject_adapter.execute()` method. That is expected. ACMM needs to know not only how to invoke an
agent, but also how that invocation became reproducible.

## Qualification checklist

Before allowing a new subject into a canonical comparative experiment, verify the following.

### Identity and reproducibility

- [ ] Agent/framework version is pinned or precisely recorded.
- [ ] Model/provider route is explicit and verified.
- [ ] Material inference settings are recorded.
- [ ] Desired-state infrastructure is version controlled.
- [ ] `doctor` detects important baseline drift.

### Isolation

- [ ] Fresh run state is created for each run.
- [ ] Memory/cross-run state is disabled or deliberately part of the subject definition.
- [ ] Background model calls are disabled or explicitly justified.
- [ ] Unrelated tool surfaces are blocked or explicitly justified.
- [ ] Synthetic/bootstrap messages have been inspected for model compatibility.
- [ ] Automatic updates or mutable external dependencies cannot silently change the baseline.

### Runtime adapter

- [ ] Machine-readable interface is used where available.
- [ ] Fixture state reaches controlled tools without hard-coded experiment logic in the adapter.
- [ ] Normal and partial evidence are captured.
- [ ] Native tool calls/results are normalized without hiding agent behavior.
- [ ] Subject/model provenance is verified where possible.
- [ ] Per-run state is cleaned up.

### Termination and failures

- [ ] Supported termination controls are declared exactly.
- [ ] Unsupported controls cause pre-run compatibility rejection.
- [ ] Timeout budget is distinguished from harness cleanup grace.
- [ ] Agent timeout/max-step outcomes remain distinct from harness errors.
- [ ] Partial evidence on bounded termination is handled deliberately.

### Qualification evidence

- [ ] Unit/infrastructure/adapter tests pass.
- [ ] Live smoke passes.
- [ ] Subject-only stress population has been exercised.
- [ ] Latency/tool/request anomalies have been inspected.
- [ ] Any baseline correction is narrow, documented, and rerun against the same stress population.
- [ ] Canonical acquisition starts only after the baseline is frozen.

## Common anti-patterns

### "The UI works, so the integration is done"

Manual UI success proves very little about reproducibility, evidence capture, or run isolation.
Prefer supported machine interfaces and managed desired state.

### "The agent is slow, therefore that is its personality"

Large latency outliers need evidence. Prompt bootstrap confusion, retries, hidden auxiliary calls,
and configuration drift can look like deep reasoning from the outside.

### "Make every agent use exactly the same internal settings"

Framework architecture is part of the subject. Normalize only what is required for a defensible
comparison; do not erase meaningful agent differences merely to make traces look similar.

### "Fix the evaluator to understand this agent's special behavior"

If the adapter can faithfully normalize a platform-specific representation into an existing ACMM
evidence concept, do that at the adapter boundary. Do not teach scientific requirements to excuse
one framework's quirks.

### "A timeout means the run is invalid"

A timeout can be a valid experimental outcome. Invalidity is reserved for evidence/acquisition
failures that make the run scientifically untrustworthy.

### "A successful tool result can be omitted because the final answer is correct"

ACMM evaluates process requirements as well as final answers. Preserve the actual trace.

### "Hidden model requests do not matter because the final answer is fine"

They may not be compliance outcomes, but they can reveal non-isolated framework behavior and can
materially alter latency/cost. Investigate them during qualification.

### "Resume the interrupted canonical execution"

Resumption complicates the meaning of one execution and can combine different runtime conditions.
Preserve the partial session for forensics and start a new canonical execution instead.

## Relationship to other documentation

This document describes the general integration process. The following documents define narrower
runtime boundaries and should be read alongside it when implementing a concrete subject:

- `docs/runtime/execution.md` for the platform-neutral subject-adapter boundary;
- `docs/runtime/experiment_execution.md` for experiment-level acquisition semantics;
- `docs/runtime/persistence.md` for atomic Run Record/evidence publication;
- `infrastructure/README.md` for the managed infrastructure lifecycle;
- `docs/runtime/<agent>_adapter.md` and `infrastructure/<agent>/README.md` for concrete
  agent-specific implementation decisions.

The general rule is that agent-specific notes may be detailed and exceptional, but they should
still fit the qualification stages described here: reproducible baseline, machine interface,
normalized evidence, exact termination semantics, layered testing, stress qualification, and a
frozen baseline before canonical experiment use.
