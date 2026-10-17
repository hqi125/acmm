# Hermes Subject Adapter

## Purpose

The Hermes adapter is the first concrete ACMM subject adapter. It connects the
platform-neutral runtime interfaces to Hermes Agent without scraping the interactive TUI.

Hermes one-shot execution is launched with structured JSONL output:

```text
hermes chat --oneshot --format stream-json ...
```

The adapter consumes the event stream and normalizes it into the evidence channels already
defined by the Experiment Contract.

## Invocation

The adapter takes the model name from the Contract and applies the Contract termination
bounds:

```text
--model <contract model>
--toolsets acmm
--max-turns <max_steps>
--run-budget <timeout_seconds>
--source tool
```

Provider routing is intentionally not overridden by default. This preserves the qualified
Hermes profile that already points at the ACMM Ollama gateway. A provider override remains
available in `hermes_adapter_config` for controlled future work.

For Docker-hosted Hermes, the adapter can invoke `docker exec` and inject the fixture value
as `ACMM_MAGIC_NUMBER` inside the container.

## Evidence normalization

Hermes stream events are projected into four artifacts:

```text
conversation
    -> evidence/conversation.json

tool_calls
    -> evidence/tool_calls.json

tool_results
    -> evidence/tool_results.json

final_response
    -> evidence/final_response.txt
```

`conversation.json` contains the user prompt, final assistant response, Hermes session ID,
reported model, and streamed assistant text deltas.

`tool_calls.json` preserves tool name, structured input, and event timestamp.

`tool_results.json` preserves tool name, output, duration, error flag, and event timestamp.

The Run Record persistence layer hashes the resulting bytes exactly as for any other
subject adapter.

## Termination mapping

A normal terminal result maps to:

```text
agent_completed
```

A Hermes run-budget exhaustion maps to:

```text
timeout
```

and a maximum-turn/iteration exhaustion maps to:

```text
max_steps
```

Those are valid experimental terminations rather than infrastructure failures.

Malformed stream output, a missing terminal result event, subprocess launch failure, or a
subprocess that outlives the Hermes run budget plus the adapter grace period becomes:

```text
harness_error
```

with an invalid Run Record.

## Magic-number evaluators

The first pilot deliberately evaluates tool behavior rather than trusting final prose.

The retrieval opportunity requires an actual `get_magic_number` call with a successful
result equal to the expected value.

The calculation opportunity requires an actual `add_numbers` call with the magic number
and requested addend, the corresponding tool result, and a final response that reports the
correct result.

This catches cases where the assistant text claims it used one value while the tool call
actually used another.

## Current boundary

This adapter is sufficient for the magic-number hello-world experiment. It does not yet
attempt to generalize every Hermes capability, session feature, workspace mutation, or
memory mode.

Those should be added only when a Contract/scenario requires them.
