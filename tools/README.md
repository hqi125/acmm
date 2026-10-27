# ACMM Standalone Tools

This directory contains independent repository programs that are useful for analysis,
diagnostics, or live smoke checks but are not part of the normal `acmm` or `acmm-infra`
command surfaces.

## `analyze_execution.py`

Runs the repository analysis workflow for a completed execution and coordinates the canonical
CSV export with the R analysis stages. The workflow writes requirement estimates, requirement
hierarchical intervals, dimension scores, and dimension hierarchical intervals as both readable text
reports and normalized CSV tables under the execution export's `analysis_output/` directory. A final
presentation stage builds `analysis_report.xlsx` with an Overview, the four normalized analysis tables,
raw Runs/Judgments sheets, and workbook metadata. The workbook is derived from the CSV outputs and does
not replace them as analysis artifacts. The wrapper keeps report tables out of the console; terminal
output is limited to workflow progress, output paths, warnings, and errors.

## `cold_start_qualification.py`

Repeats one fixed Contract run selection across independent runtime cold starts. The tool is intended
for integration debugging when a subject behaves differently immediately after startup than it does
after several consecutive runs. It is deliberately outside the ordinary `acmm run` surface because
the repeated stop/start cycle is a qualification procedure rather than an experiment execution mode.

Before every iteration, the tool explicitly stops the selected subject and verifies that it is stopped.
It then executes the requested run selection with `acmm run --start-if-needed`, allowing ACMM to own
the runtime lease and doctor the newly started subject. A nonzero experiment-run exit code is retained
as qualification evidence and does not abort later iterations. After each iteration, the tool verifies
that the subject is stopped again and forces cleanup if necessary. Failure to establish or restore a
cold-start boundary aborts the qualification because later starts would no longer be controlled.

A passive `acmm run --preview` is executed once before the loop so a bad subject/index selection fails
before consuming cold-start exposures. Runtime-log capture is enabled by default and can be disabled
with `--no-capture-runtime-logs`. The ordinary execution directories remain under the selected results
root. Qualification-level transcripts and a machine-readable summary are written under
`results/_cold_start_qualifications/qualification_<uuid>/` by default.

For the Experiment 3 Agent Zero cold-start qualification:

```text
python tools/cold_start_qualification.py \
    experiments/examples/controlled_recovery_boundary_interaction/experiment.yaml \
    --subject agent_zero_nemotron30b \
    --run-index 120-123 \
    --iterations 15
```

This produces 15 independent runtime starts and four Experiment 3 conditions per start, for 60
qualification runs total. It does not merge those runs into one execution; each cold start remains an
independent ACMM execution session, which is important when startup itself is the unit under study.

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
