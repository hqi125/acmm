# Run Record v0.1 Semantics

## Purpose

A Run Record describes one realized execution of one planned ACMM run.

It records:

- the planned-run coordinate that was realized;
- the exact Experiment Contract artifact used;
- execution termination and scientific validity;
- realized provenance;
- normalized evidence artifacts;
- one atomic judgment per scenario opportunity for valid runs.

A Run Record does not contain ACMM aggregate scores, compliance proportions, confidence
intervals, or derived scenario/run-level outcomes.

## Planned slot and realized execution

The planned slot is identified by:

```text
experiment_id
run_index
subject_id
scenario_id
repetition
```

`run.id` identifies the realized execution itself. Multiple realized executions may refer
to the same planned slot when, for example, an infrastructure failure causes a rerun.

## Contract binding

`run.contract_sha256` is the SHA-256 digest of the exact Experiment Contract artifact used
to launch the run.

The semantic validator can compare this value with the supplied Contract file digest.

## Validity and compliance

Execution validity and compliance judgments are separate concepts.

A valid run may contain compliant or violating behavior:

```text
valid run + violation
```

is ordinary scientific evidence.

An invalid run indicates that the execution itself cannot be trusted scientifically:

```text
invalid run
```

Invalid-run judgments, if present, are diagnostic only and must be excluded from ACMM
quantification.

The v0.1 validity vocabulary is:

```text
valid
invalid
```

The termination vocabulary is:

```text
agent_completed
max_steps
timeout
harness_error
```

`harness_error` requires `invalid` validity.

An invalid run must contain failure information. A valid run must not.

## Opportunity outcomes

The v0.1 opportunity outcome vocabulary is:

```text
compliant
violation
not_applicable
unadjudicable
```

For a valid run, every opportunity declared by the scenario must have exactly one judgment.

Invalid runs may contain zero or partial judgments.

`compliant` and `violation` judgments must cite at least one evidence artifact.

## Evidence manifest

Evidence is stored externally and referenced through the Run Record manifest.

Each entry contains:

```text
id
channel
artifact
sha256
```

Evidence IDs and channels are unique within a Run Record.

Artifact paths are relative POSIX-style paths rooted at the Run Record directory. Absolute
paths, backslashes, empty path segments, `.` segments, `..` traversal, and drive prefixes
are rejected.

For valid runs, every evidence channel requested by the Experiment Contract must appear
exactly once. Evidence channels not requested by the Contract are rejected.

Invalid runs may contain only the evidence successfully captured before failure.

## Timestamps

`execution.started_at` and `execution.ended_at` use RFC 3339 / JSON Schema `date-time`
format.

The end timestamp must not precede the start timestamp.

## Provenance

Provenance records the realized execution configuration. It may repeat information present
in the Experiment Contract because the meanings differ:

```text
Contract     = requested configuration
Run Record   = realized configuration
```

v0.1 requires harness, subject, and environment provenance while keeping subject and
environment configuration objects extensible.

## Validation layers

Run Record validation consists of:

```text
YAML parsing
    |
JSON Schema validation
    |
Contract-aware semantic validation
```

Schema validation enforces local structure, controlled vocabularies, required fields, and
basic conditional rules.

Semantic validation checks the record against the Experiment Contract, including planned
run binding, Contract digest, evidence completeness, opportunity completeness, reference
resolution, artifact path safety, and timestamp ordering.

Cross-record rules such as global run-ID uniqueness or deciding which valid attempt occupies
a planned slot belong to a future result-set validator.

## CLI validation

A Run Record can be checked against the exact Experiment Contract artifact with:

```text
acmm validate-record records/v0_1/run_record.example.yaml \
    --contract experiments/examples/magic_number/experiment.yaml
```

The command first validates the Experiment Contract. If the Contract is invalid, Run Record
validation is not attempted.

It then verifies the Run Record schema, computes the supplied Contract file's SHA-256, and
performs Contract-aware semantic validation.

The command's exit status describes whether the Run Record representation is valid:

```text
0 = valid Run Record
1 = validation error
2 = loading/schema/tooling error
```

`execution.validity: invalid` does not by itself cause a nonzero exit status. A correctly
formed record of an infrastructure-invalid execution is still a valid Run Record and is
reported separately as `Execution validity: invalid`.
