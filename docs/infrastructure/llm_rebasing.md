# LLM Rebasing

LLM Rebasing is the workflow for changing the shared model baseline without conflating that change
with a research experiment.

## Configuration model

The effective subject configuration is conceptually:

```text
qualified subject baseline
    + registered LLM baseline
    + optional Experiment Package integrations
    = deployed subject state
```

LLM selection belongs to `acmm env set-llm <llm-id>`. Research capability selection belongs to an
Experiment Package. Keeping those axes separate makes later comparisons interpretable.

## Context-window policy

64K is the minimum acceptable context capability for the qualified cohort, not necessarily an exact
reserved value. ACMM distinguishes:

- model-supported maximum/capability metadata;
- configured/requested context where the host exposes one; and
- observed/effective context where it can be measured.

When controllable, prefer a common configured value of at least 64K. Do not pretend capability
metadata and configured/effective context are the same observation.

## Workflow

1. Register the candidate LLM in `infrastructure/llm-baselines/catalog.yaml`.
2. Reconcile it with:

   ```text
   acmm env set-llm <llm-id>
   ```

3. Inspect passive state:

   ```text
   acmm env status
   ```

4. Run live readiness:

   ```text
   acmm env doctor --start-if-needed
   ```

5. Run the engineering behavioral qualifier:

   ```text
   python tools/qualify_llm_behavior.py
   ```

The behavioral qualifier is intentionally experiment-independent. It performs repeated closed-world
arithmetic behavior through the already-applied baseline and does not install a temporary research
tool, package, or synthetic capability. Its outputs are engineering qualification artifacts, not
ACMM research-population records.

6. Record qualification evidence and promote/freeze the baseline according to the project workflow.

## Safety properties

The qualifier requires a clean stopped baseline cohort. It does not silently select a model,
reinterpret an attached research package, or mutate research results. If package state is still
attached, explicitly detach/reconcile the baseline before qualification.
