# ACMM Standalone Tools

This directory contains independent repository programs that are useful for analysis,
diagnostics, or live smoke checks but are not part of the normal `acmm` or `acmm-infra`
command surfaces.

## `analyze_execution.py`

Runs the repository analysis workflow for a completed execution and coordinates the canonical
CSV export with the R analysis stages. The user-facing workflow is strict-first and autonomous:
when every selected planned run resolved, compliance analysis uses the strict population. When the
execution is incomplete, the wrapper emits a non-blocking warning and automatically falls back to
the available-valid compliance population. Invalid or missing runs are never converted into
compliance failures, and arbitrary R/schema/integrity errors never trigger fallback.

The general availability layer runs for every structurally valid execution regardless of whether the
compliance population remained strict or fell back. Available-valid compliance intervals remain
conditional on the observed valid population; the availability layer separately reports planned-slot
acquisition, first-attempt/retry behavior, scenario and within-repetition position imbalance,
scenario-to-slot bootstrap intervals, and a deterministic all-missing-violation to
all-missing-compliant opportunity-level sensitivity envelope. It does not assume that missing
observations are random. See `docs/analysis/availability.md`.

Routine usage therefore requires no population option:

```text
python tools/analyze_execution.py \
    results/<experiment_id>/execution_<execution_id>
```

The top-level `analysis_output/` directory is reserved for human-readable workbooks and the figures
directory. Derived tables and text diagnostics are organized underneath `data/` and `logs/`:

```text
analysis_output/
    analysis_report.xlsx
    availability_analysis.xlsx
    interaction_analysis.xlsx              # only when registered
    figures/
        interaction/*.png                  # experiment-specific when applicable
    data/
        compliance/*.csv
        availability/*.csv
        interaction/*.csv                  # only when registered
    logs/
        compliance/*.txt
        availability/*.txt
        interaction/*.txt                  # only when registered
```

`runs.csv` and `judgments.csv` remain the canonical normalized export tables one level above
`analysis_output/`; the files under `analysis_output/data/` are derived analysis tables, not raw
execution records. When rerunning analysis on an older export, the wrapper removes legacy flat
`.csv`/`.txt` analysis artifacts from `analysis_output/` so the readable top level stays clean.

## `cold_start_qualification.py`

Repeats one fixed installed Experiment Package run selection across independent runtime cold starts. The tool is intended
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

Example cold-start qualification for any installed package/run selection:

```text
python tools/cold_start_qualification.py \\
    <experiment-selector> \\
    --subject <subject-id> \\
    --run-index 0-3 \\
    --iterations 15
```

Each cold start remains an independent ACMM execution session. The tool is for engineering
qualification and does not merge those executions into one scientific population.

### LLM Rebasing behavioral qualification

After the replacement LLM has been selected with `acmm env set-llm <llm-id>` and passes
`acmm env doctor --start-if-needed`,
run the engineering-only three-agent Tool gate:

```text
python tools/qualify_llm_behavior.py --iterations 5
```

The runner discovers the deployed LLM rather than selecting one. All three runtimes must
be stopped and free of research package apparatus before it starts. It temporarily installs the dedicated engineering
controlled-Tool smoke fixture, runs the same controlled dependency path repeatedly through Hermes,
Open WebUI, and Agent Zero, stores engineering artifacts under
`results/_llm_rebasing_qualifications/`, and restores the qualified baseline Tool policy after
each subject. These artifacts are qualification evidence, not scientific experiment results.

Use this runner for LLM Rebasing. The standalone adapter smoke programs below remain useful for
focused adapter development when their required Tool apparatus is already present.

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

### Optional interaction analysis

The general execution-analysis wrapper does not select experiment-specific interaction designs by
historical experiment ID. Reusable interaction tooling remains available for packages that provide an
explicit scientifically justified design. See `docs/analysis/interaction.md` and
`analysis/r/README.md`.
