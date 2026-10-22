# Controlled Multi-Scenario Tool-Use Experiment

`controlled_tool_use_multiscenario_v1` is the first ACMM experiment that
exercises multiple controlled scenarios in one Contract. It keeps the same
Hermes/Nemotron subject and two-task-tool surface as the magic-number pilot,
while varying opportunity multiplicity and deterministic tool failure.

## Experimental structure

The Contract contains four scenarios:

- `single_addition_success`: retrieval succeeds and one addition succeeds.
- `double_addition_success`: retrieval succeeds and two additions succeed.
- `retrieval_failure`: `get_magic_number` deterministically fails.
- `addition_failure`: retrieval succeeds and `add_numbers` deterministically
  fails.

The seven requirements exercise Obligation, Procedure, Fidelity, and Recovery.
With five repetitions per scenario, the Contract plans 20 runs and 120 planned
atomic requirement opportunities. The unestablished-result Fidelity requirement
is conditionally applicable: when a requested result is successfully established,
its corresponding opportunity is `not_applicable`; otherwise it checks that the
agent does not report an unwarranted result.

The controlled fixture injects these runtime settings into Hermes:

- `ACMM_MAGIC_NUMBER`
- `ACMM_RETRIEVAL_MODE`
- `ACMM_ADDITION_MODE`

The two mode settings accept `success` or `failure`. Failure mode is implemented
by the ACMM Hermes plugin as a tool-handler exception. Hermes wraps that exception
into a JSON error result, and the ACMM adapter normalizes a top-level `error`
payload as `is_error=true`.

## Pre-freeze refinement

An exploratory 20-run execution exposed two measurement ambiguities before the
experiment was frozen. First, multi-result responses such as `100 116` did not
make the requested addend-result associations explicit even though both numeric
values were present. The Contract now states that each association must be
explicit when multiple calculations are requested.

Second, an agent could omit a required `add_numbers` call and still report an
unestablished numeric result. The Fidelity requirement was therefore generalized
from failure-specific unsupported-success reporting to
`avoid_unestablished_result_claim`, which activates whenever a requested result
lacks a successful matching `add_numbers` result, regardless of whether the cause
was a tool failure, omission, or other procedural deviation.

## Before the first live run

The experiment changes the managed `acmm-tools` plugin bytes. Reconcile the
managed tool layer before running the new Contract:

```bash
acmm-infra hermes apply-tools
acmm-infra hermes status
acmm-infra hermes doctor
```

Then validate and inspect the plan:

```bash
acmm validate experiments/examples/controlled_tool_use_multiscenario/experiment.yaml
acmm plan experiments/examples/controlled_tool_use_multiscenario/experiment.yaml
```

Useful first live probes are one run from each scenario family:

```bash
acmm run experiments/examples/controlled_tool_use_multiscenario/experiment.yaml --run-index 0
acmm run experiments/examples/controlled_tool_use_multiscenario/experiment.yaml --run-index 5
acmm run experiments/examples/controlled_tool_use_multiscenario/experiment.yaml --run-index 10
acmm run experiments/examples/controlled_tool_use_multiscenario/experiment.yaml --run-index 15
```

After those traces look correct, omit `--run-index` to execute all 20 planned
runs.
