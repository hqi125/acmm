# Agent Zero v2.14 Qualification Baseline

This note records the qualified ACMM integration baseline for Agent Zero `v2.14` with
`nemotron-3-nano:30b-cloud`. It is an integration qualification record, not Experiment 3 evidence.
The qualification runs described here must remain separate from canonical comparative acquisition.

## Baseline identity

The qualified subject is pinned by repository desired state:

- Agent Zero: `v2.14`;
- container image:
  `agent0ai/agent-zero:v2.14@sha256:b0c9763df7b8c4c5ddbdac9c101e8f84de8b7cdf9185203e446e2d7d8ebf2f1c`;
- model: `nemotron-3-nano:30b-cloud` through the managed Ollama route;
- context window: 64,000;
- Agent Profile: `acmm`;
- memory, automatic chat naming, MCP/A2A, update checking, and unrelated Skills/Tools disabled;
- experiment Tool policy supplied by the active repository apparatus.

The Agent Zero profile prompt bundle under `infrastructure/agent-zero/profile-prompts/` is part of
this pinned compatibility baseline. It is not an interchangeable collection of independent prompt
tweaks.

## Why qualification was required

Early Experiment 3 stress runs exposed two distinct integration pathologies.

The first profile used a long synthetic initial user placeholder intended to make the real task
unambiguous. It was initially fast, but in a 60-run stress population Agent Zero increasingly
reinterpreted that bootstrap message as the active request. Late repetitions showed task loss,
greeting/initialization responses, incomplete ordinary-write attempts, and increasing latency.

A first correction restored Agent Zero v2.14's inert upstream synthetic user seed, `Hello!`, while
moving continuation guidance into ACMM's solving prompt. This largely removed the late bootstrap
takeover, but exposed the opposite failure mode: completed work could be reconsidered and restarted
after Tool results. The 60-run qualification for that candidate produced two 120-second timeouts and
several redundant or malformed Tool cycles.

Inspection then found ACMM-side inconsistencies that had to be removed before those failures could
be attributed to the subject itself:

- the communication prompt required exactly `tool_name` and `tool_args`, while an inherited
  response-Tool example still demonstrated Agent Zero's legacy four-field envelope;
- the profile did not consistently explain framework Tool-result messages and `[EXTRAS]` metadata;
- duplicate-message recovery did not force a meaningfully different next action;
- Experiment 3's Agent Zero Tool prompts did not state JSON argument types as explicitly as the
  Hermes and Open WebUI interfaces, allowing quoted integer values to appear in several runs.

These were apparatus/configuration defects. The experiment task, model, controlled Tool semantics,
and evaluator rules were therefore held fixed while the Agent Zero profile was made internally
coherent.

## Qualified profile contract

The frozen v2.14 profile uses a two-field visible assistant envelope:

```text
{
  "tool_name": "<listed tool>",
  "tool_args": { ... }
}
```

This shape is justified as a qualified local-model compatibility adaptation, not as a universal or
version-independent Agent Zero protocol. ACMM only requires the observable action name, arguments,
Tool result, and final response. Removing unused visible `thoughts` and `headline` fields reduces
formatting burden for the selected model, but it also means ACMM owns the consistency of the whole
coupled prompt surface.

The following files therefore form one compatibility unit:

```text
agent.system.main.communication.md
agent.system.main.solving.md
agent.system.tools.md
agent.system.tool.response.md
fw.initial_user_message.md
fw.initial_message.md
fw.msg_repeat.md
```

The bundle preserves Agent Zero's required synthetic user-first bootstrap ordering, keeps the
upstream inert `Hello!` seed, identifies the initial user/assistant pair as bootstrap context,
identifies Tool-result history and `[EXTRAS]` as framework continuation data, prevents successful
terminal work from being restarted, removes conflicting legacy response examples, and gives
repeat-message recovery a deterministic exit path.

Experiment-specific Agent Zero Tool prompts must also state the exact JSON argument names and types.
The implementation remains strict; for example, an integer argument is not silently coerced from a
quoted JSON string.

## 60-run qualification result

The frozen candidate was stressed using all 60 Agent Zero slots of
`controlled_recovery_boundary_interaction_v1`: four scenarios interleaved across 15 repetitions.
The execution was engineering qualification only.

Qualification execution:

```text
execution_36ac5ceb-e34a-4cf2-8d77-94fe885f43bc
```

Observed result:

| Property | Qualified v2 result |
| --- | ---: |
| Selected/resolved runs | 60 / 60 |
| Timeouts | 0 |
| Intended Tool sequence | 60 / 60 |
| Quoted-string integer Tool arguments | 0 |
| Duplicate/restarted completed Tool cycles | 0 |
| Bootstrap/greeting task-loss responses | 0 |
| Prohibited `force_write_record` calls | 0 |
| Mean run duration | 4.59 s |
| Median run duration | 4.46 s |
| 95th-percentile run duration | 6.07 s |
| Maximum run duration | 7.07 s |

The exact observed Tool sequences were:

```text
45 runs: read_record -> write_record
15 runs: read_record -> write_record -> recover_write_record
```

All applicable qualification judgments were compliant: Goal 45/45, Procedure 60/60, Recovery
60/60, and Boundary 60/60. These compliance counts are evidence of integration stability only and
must not be mixed into Experiment 3's final scientific dataset.

For comparison, the immediately preceding bootstrap-only candidate required about 26 minutes for
the same 60-run population and produced two timeouts. The qualified v2 execution completed in about
5 minutes. The important result is not the speedup by itself; it is the disappearance of early
restart loops, malformed arguments, and late-run task loss under the same long-run population.

## Freeze and upgrade policy

For canonical acquisition, treat the following as one frozen subject baseline:

```text
Agent Zero v2.14
+ pinned image digest
+ managed model preset/provider route
+ ACMM profile prompt compatibility bundle
+ experiment Tool prompt/schema surface
+ Agent Zero runtime adapter
```

A change to any baseline-affecting item requires re-qualification before new results are considered
comparable. In particular, an Agent Zero version upgrade must not assume that ACMM's v2.14 prompt
overrides remain valid. Prefer the smallest coherent override surface for the new version, inspect
its native prompt contracts, and repeat live smoke plus subject-only stress qualification.

The two-field envelope is therefore frozen because it was experimentally qualified for this pinned
subject, not because ACMM assumes it is intrinsically better than Agent Zero's native four-field
protocol. A future version may validly use the native protocol if that is the more coherent and
stable integration; ACMM should normalize the observable action boundary rather than force an
internal message shape without evidence.

## Diagnostic-log artifact note

The qualification also motivated capture-boundary terminal-log sanitization. Agent Zero's raw
Docker output contains large amounts of ANSI presentation markup that is useful to an interactive
terminal but not to a persisted plain-text diagnostic artifact. Applying ACMM's
`acmm_plain_terminal_v1` sanitizer to the qualification log reduces it from 1,839,061 UTF-8 bytes
to 429,663 bytes, approximately a 76.6% reduction. The measured transformation removed 156,144
ANSI escape sequences. This reduction is an observation for that log, not a promised compression
ratio for other runtimes.

The sanitizer operates only after Docker output has been captured. It does not alter container
output, adapter evidence, Tool results, or compliance judgments. A `.meta.json` sidecar records the
sanitation identity and transformation counts for each persisted runtime log.
