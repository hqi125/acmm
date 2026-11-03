# LLM Rebasing

For the step-by-step operational procedure, see
`docs/infrastructure/llm_rebasing_workflow.md`. This document focuses on architecture, provenance,
and the rationale behind the rebasing design.

## Purpose

LLM Rebasing is the controlled process for changing ACMM's current qualified model baseline
without rewriting historical experiments or turning a model change into a repository-wide manual
edit.

The key rule is:

```text
rebasing changes the current qualified model baseline;
it does not change the model identity of frozen historical subjects.
```

Experiments 1-4 remain bound to their Nemotron subject IDs and Contract declarations. A future
replacement model receives a new LLM baseline ID and new qualified subject IDs.

## Baseline architecture

Model-specific desired state is registered under:

```text
infrastructure/llm-baselines/
|-- catalog.yaml
`-- <baseline-id>.yaml
```

`catalog.yaml` identifies the current baseline. Each baseline definition owns the shared logical
model identity and the agent-specific model settings needed to run that model through Hermes,
Open WebUI, and Agent Zero.

The agent-root profiles remain responsible for agent/framework state:

```text
infrastructure/<agent>/profile.yaml
```

They own pinned agent versions/images, deployment paths, isolation controls, memory/tool policy,
and other settings that remain meaningful independently of the selected LLM. They no longer own
the primary model name or model context setting.

The composition boundary is therefore:

```text
agent profile
    +
LLM baseline
    +
optional experiment apparatus
    =
effective deployed subject configuration
```

## Subject bindings and historical reproduction

A qualified subject binding records both its infrastructure backend and its LLM baseline ID.
For example, the historical `hermes_nemotron30b` subject is bound to
`nemotron_3_nano_30b_cloud_v1`.

When ACMM prepares a registered experiment, the canonical Contract determines its subject IDs,
and those subject bindings determine the historical LLM baseline used for provisioning. Changing
the catalog's `current` baseline therefore must not change the model used when reproducing a
frozen experiment.

A baseline-only environment apply uses the catalog's current LLM baseline and only the subject IDs
registered for that baseline.

## Context-window policy

ACMM no longer treats 64K as an eternal exact model identity. The baseline definition separates a
minimum acceptable context capability from agent-specific requested configuration.

The historical Nemotron baseline and the promoted GPT-OSS baseline both preserve the same ACMM minimum-context policy:

- minimum context window: 64,000 tokens;
- Hermes requested context: 64,000;
- Open WebUI requested context: not explicitly controlled by ACMM at this stage;
- Agent Zero primary requested context: 64,000;
- Agent Zero utility requested context: 64,000.

A non-null requested value must be at least the baseline minimum. Future context sensing should
separately report:

1. model-supported maximum context;
2. ACMM/agent requested or configured context;
3. observed/effective context exposed by the runtime.

A value above the minimum is not intrinsically invalid. Equality across subjects is preferred when
ACMM can control it, while observable mismatches should be surfaced for qualification rather than
silently hidden.

## Stage 1 checkpoint

Stage 1 intentionally keeps live behavior unchanged. It introduces the baseline registry and moves
the existing Nemotron model-specific desired state into it. The three frozen historical subject
IDs remain unchanged, and all existing Experiment Contracts retain their original model metadata.

## Stage 2: provenance and observability

Stage 2 makes the selected LLM baseline visible during environment inspection and scientific
execution before any replacement model is promoted.

Each selected subject receives runtime provenance fields derived from its registered LLM baseline,
including the baseline ID, minimum context requirement, and requested context where ACMM controls
that value. Agent Zero also records its utility-model identity and requested utility context. The
experiment execution manifest records the baseline ID resolved for every selected subject.

Agent Zero now reads its live model configuration before sending the task prompt. A mismatch in the
primary model, configured primary context, utility model, or configured utility context terminates
the attempt as a harness error rather than allowing the run to proceed under unverified model
state. Successful runs preserve the observed model/configuration fields in subject provenance.

`acmm env status` reports a normalized LLM observation for each subject:

- expected and deployed LLM baseline IDs;
- expected model and logical provider;
- baseline minimum context requirement;
- ACMM-requested context when controlled;
- observed model and context when the agent/runtime exposes them;
- the source and semantic kind of the context observation.

Context observations deliberately distinguish configured/effective values from model-capability
metadata. Hermes and Agent Zero expose configured/effective context through their live
configuration APIs. Open WebUI may expose a context value through model-catalog metadata; when it
does, ACMM labels that observation as `model_capability` rather than pretending it is the
configured/effective conversation limit. Missing Open WebUI context metadata is reported as unknown.

Cross-agent context equality is therefore an observability/qualification concern, not a new hard
`doctor` failure. ACMM still requires the registered baseline to meet the minimum context policy,
while status surfaces unequal or unknown observations for review.

The controlled smoke subject builder is also baseline-driven. It resolves model/provider/context
metadata from the current or explicitly requested baseline instead of embedding the Nemotron model
name in smoke definitions. This prepares candidate qualification to reuse the same smoke path
without editing the tools for every candidate.

## Stage 3: candidate qualification

Stage 3 introduces replacement candidates without changing the catalog's `current` pointer. The
first registered candidate is:

```text
gpt_oss_120b_cloud_candidate_v1
    model: gpt-oss:120b-cloud
    provider: ollama_cloud
    minimum context policy: 64,000
    Hermes requested context: 64,000
    Hermes reasoning effort: medium
    Open WebUI requested context: not explicitly controlled
    Agent Zero primary context: 64,000
    Agent Zero utility model: gpt-oss:120b-cloud
    Agent Zero utility context: 64,000
```

The candidate is intentionally not represented by qualified scientific subject IDs yet. An
unqualified baseline instead resolves temporary qualification subjects:

```text
hermes_llm_qualification
open_webui_llm_qualification
agent_zero_llm_qualification
```

This prevents candidate testing from making the model look scientifically frozen before the three
agents have been qualified. Experiment Packages resolve their model from registered Contract
subject bindings, while `acmm env set-llm <llm-id>` explicitly selects the registered LLM to deploy; during Stage 3 that remained the historical Nemotron baseline until
promotion evidence was complete.

Candidate selection is explicit through the state-changing `set-llm` operation:

```text
acmm env set-llm gpt_oss_120b_cloud_candidate_v1
acmm env status
acmm env doctor --start-if-needed
acmm env stop
```

`set-llm` records the deployed LLM identity beside each subject deployment. Cohort-level `status`
and `doctor` accept no cohort selectors: they passively discover and inspect all three deployed
agent environments, including mixed or partially applied baseline states. `stop` also discovers
the deployed baseline automatically, while retaining optional subject scoping because it is an
operator action.

`set-llm` belongs only to experiment-independent LLM selection. Experiment Packages resolve their
LLM through Contract subject bindings and are applied with
`acmm package apply`. Selecting a qualification baseline does not change `catalog.yaml` and
therefore does not rebase the project by itself.

The first candidate was selected as a conservative compatibility probe rather than solely on
headline benchmark performance. It offers a context capability above ACMM's 64K minimum, native
tool/structured-output support, and a `medium` reasoning-effort setting that maps naturally onto
the current Hermes baseline. Newer agentic cloud models remain useful fallback candidates if this
probe exposes adapter or runtime incompatibilities.

The current secondary shortlist is:

```text
nemotron-3-ultra:cloud
glm-5.3:cloud
deepseek-v4.1-flash:cloud
```

These names are candidate-investigation notes, not qualified ACMM baselines. Their availability
and provider behavior must be rechecked when qualification actually begins because cloud catalogs
can change independently of this repository.

## Context observability during qualification

`acmm env status` is deliberately passive. When a runtime is stopped, the baseline minimum and
requested context remain known, but live context is displayed as `N/A` rather than as an ambiguous
`unknown`. The empty cross-agent context-comparability block is omitted when no runtime exposes a
live observation.

`acmm env doctor --start-if-needed` is the primary context-observation path. Doctor captures the
live LLM observation before restoring any temporarily started runtime, then reports the configured
or effective context and its source. Hermes reads `model.context_length` from its live configuration;
Agent Zero reads the selected chat-model `ctx_length`; Open WebUI may expose model-capability context
metadata, which remains semantically distinct from configured/effective context.

Context sensing remains a qualification aid rather than a blocker for candidate registration. A
missing Open WebUI effective-context field is not itself a readiness failure when the registered
baseline satisfies the minimum capability policy.

## Provider model availability

Agent configuration alone does not establish that the configured LLM can actually be reached.
Doctor therefore requires the candidate model to be visible through each agent/provider path:

- Hermes queries the configured Ollama host model catalog directly from the running Hermes
  container;
- Open WebUI requires the model to appear in its backend model catalog;
- Agent Zero requires its provider model search to find the configured model.

All three checks are reported as `provider_model_available`. A candidate that has been configured
in the agents but has not been registered with the host Ollama installation must remain NOT READY.
For an Ollama Cloud model, register it on the Ollama host with `ollama pull <model>:cloud` before
qualification. ACMM does not silently pull or enable external models during `env set-llm`; provider
registration is an explicit host-side prerequisite and `doctor` is the readiness gate that verifies
it.

## Next rebasing step

Register the first candidate with the host Ollama installation, then rerun plain passive status and
live doctor. If all three infrastructure baselines converge and the provider model is available
through every agent path, proceed to controlled live tool-use smoke qualification before creating
permanent model-specific scientific subject IDs or changing the catalog's `current` pointer.

## Stage 4: behavioral qualification

Infrastructure readiness is necessary but not sufficient for LLM Rebasing. A candidate that is
visible through all three provider paths can still be unsuitable for ACMM if one agent cannot
reliably use its controlled Tool surface or produce normalized evidence under the replacement
model.

The engineering-only behavioral gate is:

```text
python tools/qualify_llm_behavior.py --iterations 5
```

The runner is deliberately passive about model selection. It discovers the LLM baseline already
deployed across the three-agent cohort, requires one common experiment-independent baseline, and
refuses to run while any subject runtime is active or experiment apparatus is present. Candidate
selection therefore remains an `acmm env set-llm <llm-id>` responsibility.

For each agent, the runner uses the dedicated engineering apparatus under
`tools/fixtures/llm_behavioral_tool_smoke` while retaining the deployed LLM baseline. It then starts the runtime and
executes repeated instances of the same dependency path:

```text
get_magic_number -> 87
add_numbers(87, 13) -> 100
final response reports 100
```

The smoke checker requires normalized Tool-call and Tool-result evidence, correct dependency order,
and an `agent_completed` termination. Additional successful controlled Tool calls are retained as
redundancy observations rather than being treated as automatic failures.

The Tool apparatus is removed after each subject and the experiment-independent Tool policy is
restored. These runs are not ACMM Experiment Contract executions and are never written into normal
experiment result populations. Engineering artifacts are written under:

```text
results/_llm_rebasing_qualifications/qualification_<uuid>/
```

The machine-readable summary records the deployed baseline, model/provider identity, per-subject
iterations, pass/fail state, duration, Tool-call counts, and artifact locations. Individual run
directories preserve normalized evidence and subject provenance for diagnosis.

The initial behavioral gate uses five warm repetitions per subject. Passing this gate establishes
basic live chat/Tool compatibility and short-run stability; it does not yet promote the candidate
to a scientific subject or change `catalog.yaml`.

For the final pre-promotion stability gate, the same runner can impose an independent runtime cold
start around every controlled Tool attempt:

```text
python tools/qualify_llm_behavior.py --cold-start --iterations 3
```

Cold-start mode first prepares the controlled Tool fixture, returns the subject to stopped state,
then starts the runtime separately for every iteration. The summary identifies the qualification as
`llm_cold_start_tool_smoke`, records startup duration independently from model-run duration, and
stops the runtime again before the next iteration. Three iterations per subject are the standard
Rebasing promotion gate: nine fresh-runtime attempts in total. This tests startup/provider/profile
compatibility without inflating qualification into an experiment-sized population.

The runner currently uses the dedicated engineering controlled-Tool apparatus rather than moving that source
into a shared location. This is intentional: changing the source tree of a frozen experiment would
alter historical apparatus provenance merely to support Rebasing qualification.
## Stage 4: warm behavioral qualification

GPT-OSS was exercised through all three agent frameworks using the engineering-only controlled
controlled-Tool smoke fixture. Five repeated runs per agent produced 15/15 passes, exactly 30 required
Tool calls, zero redundant Tool calls, and only `agent_completed` terminations. The qualification
artifact ID was:

```text
f554a3ef-d6ab-49ff-b82f-21b1802933b8
```

The Ollama-side request counter increased by 45 requests. This matches the ideal three model turns
per controlled Tool attempt: first Tool selection, dependent second Tool selection, and final
response. No unexplained model-call overhead was observed.

## Stage 5: cold-start stability qualification

The same candidate was then exercised with a fresh framework startup before every attempt. Three
independent cold starts per agent produced 9/9 successful Tool executions with zero redundant Tool
calls and the same required dependency path. The qualification artifact ID was:

```text
468bf401-f680-4f80-a232-94a4d76da57c
```

The Ollama-side request counter increased from 45 to 72, exactly 27 additional requests, again
matching three model turns for each of the nine attempts. Open WebUI reported a post-run cleanup
failure after all three of its Tool executions had passed; the failure was isolated to the
qualification runner attempting to restore Tool policy after the service had already been stopped.
The runner now temporarily starts Open WebUI for baseline Tool-policy restoration and returns it to
the stopped state. The successful model executions remain valid qualification evidence.

## Stage 6: promotion boundary

The qualified production baseline is:

```text
gpt_oss_120b_cloud_v1
    model: gpt-oss:120b-cloud
    provider: ollama_cloud
    minimum context policy: 64,000
```

The catalog `current` pointer now selects this baseline. Permanent scientific subject IDs are:

```text
hermes_gpt_oss120b
open_webui_gpt_oss120b
agent_zero_gpt_oss120b
```

The qualification-only `gpt_oss_120b_cloud_candidate_v1` definition remains registered so the
engineering qualification artifacts retain a resolvable baseline identity. Historical Nemotron
subjects and Experiments 1-4 remain bound to `nemotron_3_nano_30b_cloud_v1`; promotion does not
rewrite their Contracts or execution provenance. A plain baseline apply after promotion therefore
selects GPT-OSS, while an experiment apply still derives the historical model from that experiment's
subject bindings.

