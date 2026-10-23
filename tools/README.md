# ACMM Standalone Tools

This directory contains independent repository programs that are useful for analysis,
diagnostics, or live smoke checks but are not part of the normal `acmm` or `acmm-infra`
command surfaces.

## `analyze_execution.py`

Runs the repository analysis workflow for a completed execution and coordinates the canonical
CSV export with the R analysis stages.

## `smoke_agent_zero.py`

Runs one live Agent Zero/Nemotron controlled-tool smoke interaction against an already-running
and qualified Agent Zero service. It prints normalized termination, final-response, Tool-call,
and Tool-result evidence.

Prepare the service first:

```text
acmm-infra agent-zero up
acmm-infra agent-zero doctor
```

Then run:

```text
python tools/smoke_agent_zero.py
```

This program invokes the live cloud-backed model and is intentionally not part of `pytest`.
The runtime adapter design and current qualification notes live in
`docs/runtime/agent_zero_adapter.md`.
