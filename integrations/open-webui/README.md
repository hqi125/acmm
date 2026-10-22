# Open WebUI Integration

This integration uses Open WebUI's backend API rather than browser automation.

The infrastructure baseline remains responsible for the pinned Open WebUI image, provider
configuration, managed Tool source, and application readiness. The runtime adapter is
responsible for run-specific controlled Tool Valves, isolated chat state, execution, evidence
normalization, and cleanup.

## Run boundary

For each run, the adapter:

1. authenticates to the isolated no-auth Open WebUI instance;
2. sets the `acmm_tools` Valves from the prepared ACMM fixture;
3. creates a fresh saved chat;
4. submits one native-function-calling request with only `acmm_tools` selected;
5. disables web search, code interpreter, image generation, memory, and auxiliary background
   generation in the request;
6. omits `session_id`, so the backend completes the native multi-round Tool loop synchronously
   and Open WebUI built-in Tools are not added to the run;
7. reads the persisted assistant message and normalizes its structured `function_call` and
   `function_call_output` items into ACMM evidence channels; and
8. deletes the saved chat in a `finally` cleanup path.

The normalized evidence channels match the existing harness vocabulary:

```text
conversation
tool_calls
tool_results
final_response
```

The frozen Experiment 1 Contract is not modified or rebound by this integration work.

## Step-budget qualification

ACMM Contracts carry a per-run `termination.max_steps` budget. Hermes maps this to its
per-invocation `--max-turns` option. Open WebUI 0.11.4 instead bounds its native Tool loop with
the process-level `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS` startup setting (default 256).
The supported backend request used here does not provide an equivalent per-run setting.

The adapter therefore enforces the Contract timeout today, but does not claim exact per-run
`max_steps` enforcement yet. Before Open WebUI is used for acquisition where step-budget
termination could affect results, this difference must be resolved or explicitly qualified in
the experiment design. It is intentionally not hidden by hard-coding a current experiment's
budget into the general infrastructure profile.
