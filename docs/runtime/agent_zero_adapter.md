# Agent Zero Runtime Adapter

The Agent Zero adapter is the experiment-facing boundary for the pinned Agent Zero v2.14
baseline. It uses Agent Zero's supported HTTP interfaces directly and does not drive the WebUI.

## Run isolation

The persistent Agent Zero service is expected to be started once with `acmm-infra agent-zero
up` and reused across experiment runs. The adapter does not cycle Docker for individual runs.

For each run, the adapter:

1. reads Agent Zero's generated external API token through the protected settings API;
2. writes the controlled `acmm_tools` fixture values through the generic plugin-config API;
3. creates a fresh context with an explicit harness-generated context id and no project;
4. sends the scenario prompt through Agent Zero's external API with the qualified `acmm`
   Agent Profile;
5. reads the context log through the external log API;
6. normalizes controlled Tool activity into ACMM evidence;
7. terminates the context so its persisted chat is removed; and
8. restores the managed Tool plugin to its repository baseline values.

The explicit context creation step is deliberate. The external message endpoint can create a
context itself, but a client-side timeout would then occur before ACMM learned the new context
id. Pre-creating the id guarantees that cleanup remains possible even when the blocking message
request times out.

## API boundaries

Agent Zero exposes two relevant API families in this integration.

The ordinary WebUI/plugin API is session and CSRF protected. ACMM uses it for setup operations
such as reading settings, updating plugin configuration, and creating a context. The runtime
client preserves the Flask cookie, obtains `/api/csrf_token`, sends `X-CSRF-Token`, and refreshes
once after a CSRF-related 403.

The external API uses `X-API-KEY` and intentionally does not require WebUI auth or CSRF. The
adapter uses it for the blocking agent message, log retrieval, and context termination. The API
key is Agent Zero's runtime-generated `mcp_server_token`; ACMM reads it from the supported
settings response rather than persisting another secret in the repository.

## Controlled Tool state

A package-owned Tool plugin reads its state with Agent Zero's normal
`helpers.plugins.get_plugin_config(...)` resolution path. Run-specific fixture state is supplied
through the generic `ACMM_TOOL_CONFIG_JSON` envelope and saved through `/api/plugins` immediately
before execution. When cleanup restoration is enabled, the fixture must also supply
`ACMM_TOOL_BASELINE_JSON`; the adapter restores that exact package-declared baseline after the run.
ACMM does not encode study-specific configuration fields in the adapter.

## Evidence mapping

Agent Zero's Tool base class creates a `type=tool` log item before execution. The log contains a
stable call id, the original arguments, `_tool_name`, timestamp, and after execution the final
sanitized Tool response. The adapter converts those records into the same normalized evidence
shape used by Hermes and Open WebUI:

- `conversation`: user, assistant Tool-call, Tool-result, and final assistant messages in order;
- `tool_calls`: call id, normalized Tool name, input, and timestamp;
- `tool_results`: call id, Tool name, output, error flag, and timestamp; and
- `final_response`: final assistant text returned by the external message API.

All observed Agent Zero Tool log records are normalized into ACMM Tool evidence. The qualified
Agent Profile and selected package apparatus remain responsible for restricting which Tools are
available. Preserving every observed Tool record keeps the adapter experiment-independent and
makes unexpected Tool activity visible rather than silently filtering it out.

The adapter records the Agent Zero context id, Agent Profile, and execution-interface identity in
subject provenance. The context id is provenance only; the corresponding context is normally
terminated and its persisted chat removed after evidence capture.

The qualified `acmm` profile owns a coherent communication/continuation compatibility bundle for
the pinned Agent Zero v2.14 integration. It uses a two-field visible JSON Tool envelope, preserves
Agent Zero's required user-first bootstrap ordering with the inert upstream `Hello!` seed, identifies
Tool-result/framework metadata as continuation context, removes conflicting response examples, and
prevents already-completed Tool work from being restarted.

This remains profile behavior, not an adapter rewrite: the adapter preserves whatever Tool calls and
final response Agent Zero actually produces. Compatibility changes must be requalified against the
active registered LLM before they become part of the baseline.

## Termination qualification

`timeout_seconds` is enforced exactly as the timeout of the blocking external message request.
Adapter-side log capture, context termination, and Tool-baseline restoration may continue after that
subject execution budget has elapsed, but they do not extend the agent's allowed message time. On
timeout, the adapter uses the pre-created context id to retrieve the
current Agent Zero log before cleanup, normalizes any partial controlled-Tool evidence that is
already persisted, returns a valid `timeout` termination when that evidence can be captured, then
terminates the context and restores the managed Tool baseline.

Agent Zero v2.14 does not expose an exact supported per-run mapping for the Contract's
`termination.max_steps` budget. Its internal message loop continues until the agent produces a
final response or execution is otherwise interrupted. The qualified subject binding therefore
advertises timeout-only termination support, and `acmm run` rejects an Agent Zero Contract
containing `max_steps` before acquisition. A Contract requesting exact `max_steps` remains ineligible
for this binding until its termination policy is deliberately revised or an exact
upstream-supported mapping becomes available.

## Live smoke qualification

The standalone engineering smoke program lives under `tools/`:

```text
python tools/smoke_agent_zero.py
```

Run it only after the persistent Agent Zero service is qualified and ready. The smoke uses the
shared controlled-Tool engineering fixture and timeout-only termination policy. It requires the
expected Tool dependency path, normalized evidence, and correct final result; redundant successful
Tool calls and provider request counts are preserved as operational observations rather than ACMM
judgments.

The smoke is intentionally outside `pytest` because it invokes the live cloud-backed model.
