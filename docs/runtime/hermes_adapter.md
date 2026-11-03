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

The implementation keeps those responsibilities separate: `hermes.py` owns process/Docker
execution and session export orchestration, while `hermes_session.py` parses the persisted
JSONL session and projects it into canonical ACMM evidence.

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
The Hermes subject binding therefore advertises both `max_steps` and `timeout_seconds` as
supported per-run termination controls.

Hermes receives `timeout_seconds` directly as its internal `--run-budget`, so the subject execution
budget is the Contract value. The adapter allows a small outer subprocess grace only so Hermes can
exit and its persisted session can be exported; that harness grace does not extend the agent's
internal run budget.

For Docker-hosted Hermes, the adapter invokes `/opt/hermes/bin/hermes` with `docker exec`.
The prepared runtime environment supplied by the package fixture is treated only as a
string-to-string environment-variable mapping. The Hermes adapter has no knowledge of
fixture-specific concepts such as record values, workspace paths, or Tool configuration semantics.

A package fixture can provide experiment-specific environment variables without changing the Hermes
invocation code.

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

Hermes may require multiple model requests across a multi-step Tool loop. ACMM disables known
auxiliary title-generation/background-review paths in the qualified baseline so those features do not
silently add unrelated requests. Provider request counts remain operational diagnostics rather than
ACMM outcomes because retries, transport behavior, or concurrent account activity can change them.

## Termination mapping

A normal completed chat maps to `agent_completed`. Hermes run-budget exhaustion maps to
`timeout`, and maximum-turn/iteration exhaustion maps to `max_steps`; these are valid bounded
experimental terminations.

Failure to launch Hermes, obtain/export the session, parse the exported evidence, or verify
the subject model maps to `harness_error` and therefore an invalid Run Record.

## Evaluator boundary

The Hermes adapter normalizes evidence; it does not know a package's requirement semantics. Package
runtime evaluators judge the resulting normalized Tool calls/results and final response. This keeps
one adapter reusable across studies.

## Managed infrastructure

Hermes baseline state lives under `infrastructure/hermes/`. Research plugins/toolsets are supplied by
the selected installed Experiment Package from its staged apparatus root. Package application is
performed through:

```text
acmm package apply <experiment-selector>
```

or permissioned per-execution reconciliation with `acmm run <selector> --apply-if-needed`.
`acmm-infra hermes apply` remains baseline-only.

## Live execution

Run an installed package through the common execution surface:

```text
acmm run <experiment-selector> --run-index 0 --subject <hermes-subject-id>
```

The package runtime declares required Tool capabilities while infrastructure owns the qualified Hermes
baseline. `acmm run` verifies package deployment and live readiness before acquisition.
