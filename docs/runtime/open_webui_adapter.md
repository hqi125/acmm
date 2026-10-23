# Open WebUI Runtime Adapter

The Open WebUI adapter is the experiment-facing boundary for the pinned Open WebUI 0.11.4
baseline. It talks to the backend API directly; browser UI state is outside the execution path.


## Runtime boundary

The infrastructure baseline owns the pinned Open WebUI image, provider configuration,
application isolation, managed Tool source, and readiness qualification. The runtime adapter
reuses that qualified service; it owns only run-specific Tool Valves, fresh chat state,
execution, evidence normalization, and cleanup. Browser automation is never part of the run
path.

## Isolation model

A run receives fresh conversation state rather than reusing an earlier chat. Run-specific
controlled Tool state is supplied through `acmm_tools` Valves immediately before execution.
The adapter creates one saved chat, executes the task, captures persisted structured Tool
activity, and deletes the chat afterward. Cleanup is attempted even when evidence parsing
fails.

No `session_id` is sent on the completion request. This keeps Open WebUI built-in Tools out of
the native Tool surface while allowing the selected workspace Tool to execute in the backend's
blocking multi-round loop. Request feature flags also explicitly disable web search, code
interpreter, image generation, and memory. Title, tag, and follow-up background generation are
disabled for the run. These request controls intentionally duplicate the infrastructure-level
experiment-isolation baseline so execution remains narrow even if an application setting later
drifts.

## Evidence mapping

Open WebUI 0.11.4 persists native Tool activity as `function_call` and
`function_call_output` items on the assistant message. The adapter converts those records into
the same normalized evidence shape used by the existing ACMM evaluators:

- `conversation`: user, assistant Tool-call, Tool-result, and final assistant messages in order;
- `tool_calls`: call id, Tool name, normalized input, and timestamp;
- `tool_results`: call id, Tool name, output, error flag, and timestamp; and
- `final_response`: final assistant text.

The adapter also records the Open WebUI chat id and execution interface in subject provenance.
The chat id is provenance only; the corresponding chat is normally deleted after capture.

## Termination

`timeout_seconds` is enforced at the HTTP execution boundary with a small adapter transport
allowance. Open WebUI 0.11.4's native Tool-loop iteration limit is configured at process
startup through `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS`, rather than through the individual
chat request. Consequently, the Contract's per-run `max_steps` budget has no exact supported
mapping in this adapter. The qualified subject binding therefore advertises timeout-only
termination support, and `acmm run` rejects an Open WebUI Contract containing `max_steps` before
acquisition. The frozen Experiment 1 Contract remains ineligible for this binding until its
termination policy is deliberately revised or an exact upstream-supported mapping becomes
available.
## Live smoke qualification

The repository provides `tools/smoke_open_webui.py` for a live end-to-end qualification run
against an already-running Open WebUI service. The smoke uses the same controlled success path
as the Agent Zero smoke: `get_magic_number` must return 87, `add_numbers` must add 13, and the
agent must report the resulting value.

Prepare the qualified service with `acmm-infra open-webui up` and
`acmm-infra open-webui doctor`, then run `python tools/smoke_open_webui.py`. The program is
intentionally outside `pytest` because it invokes the live cloud-backed model.

The first live qualification run passed end to end: Open WebUI executed exactly the required
`get_magic_number` and `add_numbers` calls, reported 100, and produced three upstream Ollama
model requests with no redundant controlled Tool calls.

The smoke now shares its controlled success fixture and checker with Agent Zero. End-to-end
correctness is the pass criterion; redundant controlled Tool calls and upstream request count are
reported as operational observations rather than silently tuned away or treated as experiment
judgments.

