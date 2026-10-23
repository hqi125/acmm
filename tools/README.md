# ACMM Standalone Tools

This directory contains independent repository programs that are useful for analysis,
diagnostics, or live smoke checks but are not part of the normal `acmm` or `acmm-infra`
command surfaces.

## `analyze_execution.py`

Runs the repository analysis workflow for a completed execution and coordinates the canonical
CSV export with the R analysis stages.

## Controlled-tool smoke programs

`smoke_agent_zero.py` and `smoke_open_webui.py` exercise the same controlled success fixture:
`get_magic_number` must yield 87, `add_numbers` must then add 13, and the final response must
report 100. Both programs share the checker in `controlled_tool_smoke.py`.

The smoke checker treats end-to-end correctness as the qualification criterion. Both live smoke
programs use the same timeout-only termination policy (`timeout_seconds: 120`). The checker
requires an `agent_completed` termination, parseable normalized evidence, the required
retrieval-then-addition dependency path, and a final response reporting 100. Additional successful
controlled Tool calls are reported as redundant-call observations rather than infrastructure
failures. Upstream model request count is also an external observation and is intentionally not a
smoke pass criterion.

These programs invoke the live cloud-backed model and are intentionally outside `pytest`.

### Agent Zero

Prepare the service first:

```text
acmm-infra agent-zero up
acmm-infra agent-zero doctor
```

Then run:

```text
python tools/smoke_agent_zero.py
```

The runtime adapter design and current qualification notes live in
`docs/runtime/agent_zero_adapter.md`.

### Open WebUI

Prepare the service first:

```text
acmm-infra open-webui up
acmm-infra open-webui doctor
```

Then run:

```text
python tools/smoke_open_webui.py
```

The runtime adapter design and qualification notes live in
`docs/runtime/open_webui_adapter.md`.
