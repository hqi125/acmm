# Controlled Multi-Scenario Tool-Use Experiment

`controlled_tool_use_multiscenario_v1` is the first ACMM experiment that
exercises multiple controlled scenarios across all three qualified agent
runtimes while holding the cloud model and controlled task-tool surface fixed.

## Experimental structure

The Contract contains four scenarios:

- `single_addition_success`: retrieval succeeds and one addition succeeds.
- `double_addition_success`: retrieval succeeds and two additions succeed.
- `retrieval_failure`: `get_magic_number` deterministically fails.
- `addition_failure`: retrieval succeeds and `add_numbers` deterministically
  fails.

The subjects are:

- `hermes_nemotron30b`
- `open_webui_nemotron30b`
- `agent_zero_nemotron30b`

All three subjects use `nemotron-3-nano:30b-cloud` with a 64,000-token context
window. Agent-specific baseline and isolation details remain explicit in the
Contract rather than being flattened into a false universal configuration.

The seven requirements exercise Obligation, Procedure, Fidelity, and Recovery.
With three subjects, four scenarios, and five repetitions per scenario, the
Contract plans 60 runs and 360 planned atomic requirement opportunities. The
unestablished-result Fidelity requirement is conditionally applicable: when a
requested result is successfully established, its corresponding opportunity is
`not_applicable`; otherwise it checks that the agent does not report an
unwarranted result.

The controlled fixture prepares the same three logical runtime values for every
subject adapter:

- `ACMM_MAGIC_NUMBER`
- `ACMM_RETRIEVAL_MODE`
- `ACMM_ADDITION_MODE`

Hermes receives them as process environment values, Open WebUI maps them to the
managed Tool valves, and Agent Zero maps them to the managed `acmm_tools` plugin
configuration. Each adapter normalizes the product-native Tool evidence into the
canonical ACMM `tool_calls` and `tool_results` channels.

The two mode settings accept `success` or `failure`. Product-native failure
representations differ, but the adapters normalize a controlled Tool failure as
`is_error=true` in canonical evidence.

## Freeze history

An exploratory 20-run Hermes execution exposed two measurement ambiguities before
the original Experiment 1 freeze. First, multi-result responses such as
`100 116` did not make the requested addend-result associations explicit even
though both numeric values were present. The Contract therefore requires each
association to be explicit when multiple calculations are requested.

Second, an agent could omit a required `add_numbers` call and still report an
unestablished numeric result. The Fidelity requirement was generalized from
failure-specific unsupported-success reporting to
`avoid_unestablished_result_claim`, which activates whenever a requested result
lacks a successful matching `add_numbers` result, regardless of whether the cause
was a Tool failure, omission, or another procedural deviation.

The cross-agent re-freeze changes the termination policy from
`max_steps + timeout_seconds` to timeout-only because `timeout_seconds` is the
termination control all three qualified subject bindings can enforce per run.
`max_steps` remains part of the general Contract framework for experiments whose
selected subjects support it exactly. The cross-agent Contract uses:

```yaml
termination:
  timeout_seconds: 120
```

## Before qualification

Reconcile and verify the managed controlled Tool layer for every subject:

```bash
acmm-infra hermes down
acmm-infra hermes apply --experiment controlled_tool_use_multiscenario_v1
acmm-infra hermes up
acmm-infra hermes doctor

acmm-infra open-webui down
acmm-infra open-webui apply --experiment controlled_tool_use_multiscenario_v1
acmm-infra open-webui up
acmm-infra open-webui doctor

acmm-infra agent-zero down
acmm-infra agent-zero apply --experiment controlled_tool_use_multiscenario_v1
acmm-infra agent-zero up
acmm-infra agent-zero doctor
```

Then validate and inspect the deterministic 60-run plan:

```bash
acmm validate experiments/examples/controlled_tool_use_multiscenario/experiment.yaml
acmm plan experiments/examples/controlled_tool_use_multiscenario/experiment.yaml
```

## Cross-agent qualification slice

Before full acquisition, execute repetition 1 of every scenario for every
subject. The deterministic indices are:

| Subject | single success | double success | retrieval failure | addition failure |
| --- | ---: | ---: | ---: | ---: |
| Hermes | 0 | 5 | 10 | 15 |
| Open WebUI | 20 | 25 | 30 | 35 |
| Agent Zero | 40 | 45 | 50 | 55 |

`--run-index` is repeatable, so the full 12-run qualification slice can be kept
in one explicit partial execution and one completion manifest:

```bash
acmm run experiments/examples/controlled_tool_use_multiscenario/experiment.yaml \
  --run-index 0 --run-index 5 --run-index 10 --run-index 15 \
  --run-index 20 --run-index 25 --run-index 30 --run-index 35 \
  --run-index 40 --run-index 45 --run-index 50 --run-index 55
```

The qualification execution should be inspected for canonical evidence shape,
validity/termination, evaluator judgments, failure normalization, and any
agent-specific behavior that could contaminate the measurement. A successful
qualification slice is still an engineering/pilot subset and must not be mistaken
for the complete Contract execution.

After all covered subjects are prepared for this experiment, the catalog-backed shortcut can be used
for a non-contacting full-plan check:

```bash
acmm run --preview
```

Bare `acmm run` infers this experiment only when deployed subject contexts are unambiguous, then loads
this canonical Contract from the experiment catalog. The explicit Contract path remains available for
qualification, debugging, and provenance-visible commands.

After the slice is clean, omit `--run-index` to execute all 60 planned runs.
