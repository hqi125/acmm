# Hermes Subject Adapter

## Purpose

The Hermes adapter is the first concrete ACMM subject adapter. It connects the
platform-neutral experiment runtime to the pinned Hermes Agent v0.21.3 deployment without
scraping the interactive TUI.

## Pinned execution interface

Hermes v0.21.3 does not provide the later `chat --format stream-json` interface. ACMM uses
the machine-readable facilities that actually exist in the pinned image.

The adapter performs two steps:

```text
1. hermes chat --oneshot -Q ...
       -> final stdout
       -> session_id on stderr

2. hermes sessions export - --session-id <id> --format jsonl
       -> persisted Hermes session
```

The first command performs the subject interaction. The second is evidence acquisition only;
it does not call the model. ACMM normalizes the exported persisted session into the evidence
channels required by the Experiment Contract.

The adapter intentionally does not pass `--model` or `--provider` in the normal qualified
path. `acmm-infra hermes doctor` has already verified the configured model/provider route,
and the adapter verifies that the exported session reports the Contract's expected model.

## Invocation

Per-run controls supplied by the Contract/runtime are:

```text
--toolsets acmm
--max-turns <max_steps>
--run-budget <timeout_seconds>
--source tool
--query <scenario prompt>
```

For Docker-hosted Hermes, the adapter invokes `/opt/hermes/bin/hermes` with `docker exec`
and injects the fixture value as `ACMM_MAGIC_NUMBER`.

## Evidence normalization

The exported Hermes session is projected into:

```text
conversation   -> evidence/conversation.json
tool_calls     -> evidence/tool_calls.json
tool_results   -> evidence/tool_results.json
final_response -> evidence/final_response.txt
```

`conversation.json` preserves the exported session messages, session ID, and reported model.

`tool_calls.json` stores normalized tool name, structured input, call ID, and timestamp when
present. `tool_results.json` stores normalized tool name, output, error status, call ID, and
timestamp when present.

Hermes Tool Search can represent a deferred local tool through the transport bridge
`tool_call`, whose argument contains the underlying tool name and arguments. ACMM's
normalizer defensively unwraps a single underlying local call and records the bridge as
`transport_name`. Evaluators therefore judge the real executed tool rather than Hermes's
routing wrapper.

The ACMM experiment-isolation baseline disables Tool Search for new experimental runs, so
`acmm` tools should normally be exposed directly. Bridge normalization remains as a
compatibility/forensics safeguard for persisted sessions.

## Request accounting

For the magic-number pilot, the two tools are causally dependent:

```text
model call 1 -> get_magic_number
tool result  -> 87
model call 2 -> add_numbers(87, 13)
tool result  -> 100
model call 3 -> final response
```

Three model requests are therefore the normal successful main-agent path. Hermes also has
auxiliary features that can make model calls independently of this loop, notably automatic
session-title generation. ACMM disables title generation and background review in its
experiment-isolation profile so those auxiliary calls do not contaminate request accounting.

The request count is an operational diagnostic, not an ACMM outcome: provider retry or
transport failure can still create additional upstream requests.

## Termination mapping

A normal completed chat maps to `agent_completed`. Hermes run-budget exhaustion maps to
`timeout`, and maximum-turn/iteration exhaustion maps to `max_steps`; these are valid bounded
experimental terminations.

Failure to launch Hermes, obtain/export the session, parse the exported evidence, or verify
the subject model maps to `harness_error` and therefore an invalid Run Record.

## Magic-number evaluators

The pilot evaluates actual tool behavior rather than trusting final prose.

The retrieval opportunity requires an actual normalized `get_magic_number` call with a
successful result equal to the expected value.

The calculation opportunity requires an actual normalized `add_numbers` call using the
retrieved magic number and requested addend, its corresponding successful result, and a
final response reporting the correct result.

## Managed infrastructure

The repository-owned plugin source is:

```text
infrastructure/hermes/plugins/acmm-tools/
```

Before a live experiment, `acmm-infra hermes doctor` must report `READY`. If only managed
tool/plugin code changed after the baseline was established, reconcile that narrow scope
with:

```text
acmm-infra hermes down
acmm-infra hermes apply-tools
acmm-infra hermes up
acmm-infra hermes doctor
```

A change to the model route, Blank Slate baseline, or ACMM experiment-isolation settings
requires the full `acmm-infra hermes apply` path instead.

## Live pilot command

The single-slot smoke test is:

```text
acmm run experiments/examples/magic_number/experiment.yaml --run-index 0
```

The experiment layer explicitly requests the `acmm` toolset for the run; infrastructure
continues to own the qualified subject baseline.

The magic-number Contract records the ACMM experiment-isolation controls under the subject's
open `configuration` mapping. Those values identify the qualified configuration required by
the run; `acmm-infra hermes doctor` remains the observed-state gate that verifies the live
Hermes deployment before acquisition. The Run Record additionally records run-time subject
facts such as Hermes's reported model and session ID.
