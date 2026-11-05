# ACMM Engineering Tools

The `tools/` directory contains engineering and analysis utilities. These programs are not research
Experiment Packages unless explicitly stated otherwise.

## Analysis

`analyze_execution.py` drives the normalized export/statistical workflow for completed executions.
Its outputs remain separate from raw evidence and persisted Run Records.

## Cold-start qualification

`cold_start_qualification.py` measures subject runtime bring-up behavior while preserving controlled
startup boundaries. Use it for infrastructure diagnosis rather than compliance scoring.

## LLM behavioral qualification

`qualify_llm_behavior.py` performs the LLM Rebasing behavioral smoke. It discovers the already-applied
baseline, requires the cohort to be in clean experiment-independent state, starts subjects as needed,
and repeats the same closed-world arithmetic task through each agent.

The task implementation lives in `llm_behavioral_smoke.py`. No temporary research Tool, plugin, or
Experiment Package is installed. Qualification artifacts are engineering evidence and are not added
to research populations.

Standalone `smoke_open_webui.py` and `smoke_agent_zero.py` remain focused adapter/host development
utilities. They exercise the current baseline interfaces and do not provide a hidden research tool
configuration path.
