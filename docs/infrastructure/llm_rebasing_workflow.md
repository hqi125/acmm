# LLM Rebasing Operator Workflow

This is the concise operator sequence for qualifying a replacement LLM baseline.

## 1. Reconcile the candidate

```text
acmm env set-llm <llm-id>
```

All qualified subjects should be stopped before baseline reconciliation.

## 2. Inspect deployed state

```text
acmm env status
```

Check the model identity, provider, context observation, and that no research Experiment Package is
attached.

## 3. Run live doctor

```text
acmm env doctor --start-if-needed
```

Doctor may temporarily start stopped subjects, perform supported live checks, then restore their
initial stopped state.

## 4. Run behavioral qualification

```text
python tools/qualify_llm_behavior.py
```

The qualifier performs repeated closed-world arithmetic prompts against each qualified host using
the already-deployed model. It does not install a temporary tool or research package.

Use `--help` for repetition/cold-start/output options.

## 5. Interpret failures

- **Model unavailable:** confirm provider authentication and candidate registration.
- **Context observation below policy:** distinguish configured/effective observations from
  model-capability metadata before changing configuration.
- **Research package still attached:** detach the package or re-run `acmm env set-llm <llm-id>` to
  restore baseline state; the qualifier will not invent a replacement scientific context.
- **Runtime startup failure:** diagnose the subject manager independently before attributing the
  failure to model behavior.
- **Behavioral mismatch:** retain the engineering artifact and investigate before promoting the
  candidate.

## 6. Freeze the promoted baseline

Once qualification is accepted, record the selected catalog ID and relevant qualification evidence in
the study records. Later experiments should reference that frozen baseline rather than silently
changing models mid-series.
