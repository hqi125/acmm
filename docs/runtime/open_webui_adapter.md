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

`timeout_seconds` is enforced exactly as the timeout of the blocking HTTP completion request.
Adapter-side evidence capture and chat cleanup may continue after that subject execution budget has
elapsed, but they do not extend the agent's allowed completion time. On timeout, the adapter reads
the persisted chat before cleanup and preserves any controlled-Tool evidence already available.
Open WebUI 0.11.4's native Tool-loop iteration limit is configured at process
startup through `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS`, rather than through the individual
chat request. Consequently, the Contract's per-run `max_steps` budget has no exact supported
mapping in this adapter. The qualified subject binding therefore advertises timeout-only
termination support, and `acmm run` rejects an Open WebUI Contract containing `max_steps` before
acquisition. A Contract requesting exact `max_steps` remains ineligible for this binding until its
termination policy is deliberately revised or an exact upstream-supported mapping becomes
available.
## Live smoke qualification

`tools/smoke_open_webui.py` provides a live engineering qualification against an already-running
Open WebUI service. It uses the shared controlled-Tool smoke fixture also used for Agent Zero.

Prepare the service with:

```text
acmm-infra open-webui up
acmm-infra open-webui doctor
python tools/smoke_open_webui.py
```

The smoke is intentionally outside `pytest` because it invokes the live cloud-backed model. The
pass criterion is end-to-end controlled Tool correctness with normalized evidence; redundant Tool
calls and provider request counts are reported as operational observations rather than scientific
experiment outcomes.
