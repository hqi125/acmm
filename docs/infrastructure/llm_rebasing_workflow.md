# LLM Rebasing Workflow

This document is the operational workflow for replacing ACMM's current qualified LLM baseline
without rewriting historical experiments. Architectural rationale and context-window policy live in
`docs/infrastructure/llm_rebasing.md`; qualification evidence for the current GPT-OSS baseline lives
in `docs/infrastructure/gpt_oss_120b_qualification.md`.

## Core rule

LLM Rebasing changes the project's **current qualified baseline**. It does not rewrite frozen
Experiment Contracts, historical subject IDs, or prior execution provenance.

The workflow separates four identities:

```text
historical experiment subject
    -> historical LLM baseline

candidate LLM baseline
    -> engineering-only qualification subjects

promoted LLM baseline
    -> permanent scientific subject IDs

catalog current pointer
    -> default baseline for experiment-independent operations
```

Experiment Package application follows the package Contract's registered subjects and their bound
LLM baselines. Experiment-independent model selection is explicit with
`acmm env set-llm <llm-id>`; the CLI does not implicitly choose the catalog's current entry.

## 1. Register a candidate without promoting it

Add a baseline definition under:

```text
infrastructure/llm-baselines/<candidate-id>.yaml
```

Do not change the catalog `current` pointer yet. A candidate may omit permanent scientific subject
IDs; ACMM will use engineering-only qualification identities such as:

```text
hermes_llm_qualification
open_webui_llm_qualification
agent_zero_llm_qualification
```

Keep model-specific configuration in the LLM baseline rather than copying it into the three agent
profiles. The baseline records the logical model/provider identity, minimum context requirement,
and per-agent requested settings where ACMM can control them.

## 2. Make the provider model available explicitly

For Ollama Cloud, register the candidate on the Ollama host before expecting the agents to be ready.
For example:

```text
ollama pull <model>:cloud
ollama ls
```

ACMM intentionally does not pull external models during `env set-llm`. Provider registration is
an operator action; `doctor` verifies that the configured model is actually visible through each
agent/provider path.

## 3. Set the candidate qualification LLM

Candidate selection uses the state-changing `set-llm` operation:

```text
acmm env set-llm <candidate-id>
```

After `set-llm`, observational cohort commands are passive and comprehensive:

```text
acmm env status
acmm env doctor --start-if-needed
```

Do not pass a baseline or subject selector to `status` or `doctor`. They discover all three deployed
agent environments and report the state that actually exists, including mixed or partially applied
states.

A candidate must pass live provider-model availability for Hermes, Open WebUI, and Agent Zero.
Doctor also reports live context observations where the framework exposes them.

## 4. Interpret context-window evidence conservatively

ACMM treats 64K as a minimum context capability, not an eternal exact model identity.

Keep these concepts separate:

1. model-supported maximum context;
2. ACMM/agent requested or configured context;
3. observed/effective context exposed by the live runtime.

`acmm env status` is passive. For stopped runtimes, live context is `N/A`; this is not a readiness
failure. `acmm env doctor --start-if-needed` is the preferred live observation path.

Hermes and Agent Zero can expose configured/effective context. Open WebUI may expose only model
capability metadata; ACMM must not relabel that as an effective configured limit. Values above the
64K minimum are acceptable. Equality across agents is preferred when controllable and otherwise
reported as a comparability observation.

## 5. Run warm behavioral qualification

Once infrastructure doctor is READY across all three agents, return the cohort to stopped state and
run:

```text
python tools/qualify_llm_behavior.py --iterations 5
```

The runner is model-selection passive. It discovers the deployed baseline, requires a clean
experiment-independent stopped cohort, temporarily installs the dedicated engineering controlled-Tool fixture,
and tests:

```text
get_magic_number -> 87
add_numbers(87, 13) -> 100
final response reports 100
```

Qualification artifacts are engineering evidence under:

```text
results/_llm_rebasing_qualifications/qualification_<uuid>/
```

They are not scientific experiment results. Record pass/fail state, normalized evidence, latency,
redundant Tool calls, and the external Ollama request count when available.

## 6. Run cold-start stability qualification

After the warm gate passes, run:

```text
python tools/qualify_llm_behavior.py --cold-start --iterations 3
```

This performs three independent runtime starts per agent, nine Tool attempts total. Startup latency
is recorded separately from model execution latency. The purpose is framework/model initialization
stability, especially for agents whose prompts or model presets are model-sensitive.

A cleanup failure that occurs only after successful model executions must be classified separately
from the model qualification result. Fix the harness and preserve valid execution evidence when the
failure cannot have changed those completed runs.

## 7. Promote only after both gates pass

Promotion consists of:

1. creating a production baseline ID distinct from any candidate ID;
2. registering permanent model-specific scientific subject IDs for all three agents;
3. changing `infrastructure/llm-baselines/catalog.yaml` so `current` points to the production
   baseline;
4. preserving the candidate definition so qualification artifacts remain resolvable;
5. preserving all historical baseline definitions and subject bindings;
6. documenting the qualification evidence and promotion boundary.

After promotion, converge the deployed environment onto the production identity explicitly:

```text
acmm env set-llm <production-id>
acmm env status
acmm env doctor --start-if-needed
```

The actual model settings may be identical to the candidate, but `set-llm` replaces temporary
qualification deployment identity with permanent production subject/baseline identity.

## 8. Verify historical reproduction remains intact

Before freezing Rebasing, verify that historical package Contracts still resolve to their declared subjects
and LLM baseline. Promotion must never make an old Experiment Contract silently follow the new
catalog `current` pointer.

Conceptually:

```text
plain baseline operation
    -> current promoted baseline

experiment-aware operation
    -> Contract subject
    -> registered historical baseline
```

Retrospective reruns under a new LLM are a separate scientific/comparative activity, not part of
infrastructure Rebasing.

## Recovery and troubleshooting

### Candidate configured but doctor says model unavailable

Confirm the model exists on the Ollama host:

```text
ollama ls
ollama pull <model>:cloud
```

Then rerun:

```text
acmm env doctor --start-if-needed
```

No additional `set-llm` is needed if the agents are already configured for that model.

### Qualification reports stale or inconsistent experiment apparatus

The qualification Tool fixture temporarily uses experiment apparatus markers. An interrupted or
older buggy cleanup can leave one behind. Reconcile the environment to the intended LLM explicitly:

```text
acmm env set-llm <llm-id>
```

Then confirm:

```text
acmm env status
acmm env doctor --start-if-needed
```

and rerun qualification. The qualifier does not silently reinterpret or delete inconsistent
apparatus because passive discovery should never invent scientific subject identity.

### Status shows `N/A` for live context

This is expected when runtimes are stopped. Use:

```text
acmm env doctor --start-if-needed
```

for live context observations. Missing effective context metadata from a framework is an
observability limitation, not automatically a readiness failure, provided the registered baseline
meets the minimum policy.

## Current rebasing boundary

The first completed ACMM LLM Rebase promoted `gpt-oss:120b-cloud` as
`gpt_oss_120b_cloud_v1`. Experiments 1-4 remain historical Nemotron experiments bound to
`nemotron_3_nano_30b_cloud_v1`.
