# GPT-OSS 120B LLM Rebasing Qualification

## Decision

`gpt-oss:120b-cloud` is qualified as ACMM's current LLM baseline under
`gpt_oss_120b_cloud_v1`.

The historical Nemotron baseline remains registered for reproduction of Experiments 1-4.

## Infrastructure gate

All three agents successfully applied the GPT-OSS candidate baseline and passed live environment
readiness after the model was made available through the host Ollama installation. Doctor verifies
provider-side model availability for Hermes, Open WebUI, and Agent Zero and captures live context
observations where the framework exposes them.

## Warm behavioral gate

Qualification ID: `f554a3ef-d6ab-49ff-b82f-21b1802933b8`

- Hermes: 5/5 passed.
- Open WebUI: 5/5 passed.
- Agent Zero: 5/5 passed.
- Required controlled Tool calls: 30.
- Redundant Tool calls: 0.
- Ollama requests attributed to this gate: 45.

Each attempt followed `get_magic_number -> add_numbers -> final response` and terminated with
`agent_completed`.

## Cold-start gate

Qualification ID: `468bf401-f680-4f80-a232-94a4d76da57c`

- Hermes: 3/3 passed.
- Open WebUI: 3/3 passed.
- Agent Zero: 3/3 passed.
- Required controlled Tool calls: 18.
- Redundant Tool calls: 0.
- Additional Ollama requests attributed to this gate: 27.

The Open WebUI qualification runner reported a cleanup failure only after all three cold-start Tool
runs had passed. The failure was caused by baseline Tool-policy restoration being attempted while
Open WebUI was stopped. The runner was corrected to start Open WebUI temporarily for cleanup and
restore the stopped state afterward. No model rerun is required for this harness-only defect.

## Promotion

The production baseline is `gpt_oss_120b_cloud_v1`. The permanent subject IDs are:

- `hermes_gpt_oss120b`
- `open_webui_gpt_oss120b`
- `agent_zero_gpt_oss120b`

The qualification-only baseline ID `gpt_oss_120b_cloud_candidate_v1` remains registered for
provenance. Historical subject IDs remain unchanged.
