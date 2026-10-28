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

### Supplementary experiment analysis

`tools/analyze_execution.py` can run experiment-registered supplementary analyses after the stable
core workflow. Interaction analysis is a generic opt-in capability: an experiment registers an
interaction-design specification when its scientific design calls for factorial interaction
estimands. Experiment 3 (`controlled_recovery_boundary_interaction_v1`) is currently the first
registered interaction design. Its outputs are deliberately separate from the core workbook:

```text
analysis_output/analysis_report.xlsx
analysis_output/availability_analysis.xlsx
analysis_output/interaction_analysis.xlsx
analysis_output/figures/interaction/*.png
```

The standalone figures are generated by R and are also embedded in the supplementary workbook.
Experiments without a registered interaction design still receive the core compliance report and
general availability workbook, but no interaction workbook. Experiment 4 intentionally follows this
path: its four transition cells are analyzed through the planned Goal/Procedure/Fidelity estimands,
not through an automatically inferred factorial interaction.
