# ACMM Experiment Contract v0.1 Semantics

## 1. Purpose

The ACMM Experiment Contract defines the configuration and normative structure of an
experiment before execution. It specifies what is being tested, which requirements are
relevant, where those requirements are evaluated, which checks adjudicate them, what
evidence must be captured, and how repeated runs are initialized and bounded.

The Contract does not contain run outcomes, ACMM scores, estimators, aggregation,
confidence intervals, or derived scenario/run-level compliance results.

## 2. Core objects

### Experiment

The `experiment` object gives the Contract a stable identity and optional descriptive
metadata.

### Subject

A subject identifies the agent/model configuration under evaluation. Subject metadata
describes the scientific object being tested; platform-specific connection details,
container names, API endpoints, and adapter implementation details belong to harness
configuration and execution provenance.

### Requirement

A requirement is a normative statement that belongs to one ACMM dimension.

Each requirement contains:

- `statement`: what behavior is required;
- `applicability`: when the requirement is relevant;
- `compliance`: what behavior satisfies the requirement.

The text is experiment-specific natural language. The associated `dimension` uses the
controlled ACMM vocabulary defined by the schema.

### Scenario

A scenario defines one concrete task condition presented to a subject. It may identify an
environment fixture, scenario-specific configuration, and the capabilities exposed to the
subject.

A scenario must declare at least one opportunity.

### Opportunity

An opportunity is a concrete evaluation locus for exactly one requirement within a
scenario.

In v0.1, every opportunity references exactly one requirement and one check.

Opportunity identifiers are local to their containing scenario. Requirements and checks
may be reused across scenarios and opportunities.

### Check

A check identifies the evaluator used to adjudicate one or more opportunities. The
Contract names the evaluator symbolically; the harness resolves that name to an executable
implementation.

A check may contain evaluator-specific parameters.

### Evidence policy

The evidence policy declares the normalized evidence channels that must be captured during
execution. The harness determines how platform-native observations are converted into
these normalized evidence categories.

### Run protocol

The run protocol defines repetition count, optional deterministic pacing, reset behavior,
and termination bounds.

`pacing.inter_run_delay_seconds` is an optional non-negative fixed delay between selected
planned slots. If omitted, the delay is zero. The delay is applied after a slot has either
resolved or exhausted its allowed acquisition attempts and before the next selected slot
begins. It is not applied between retries of the same slot and is never applied after the
final selected slot. Fixed pacing is part of the Contract so acquisition timing policy is
visible and reproducible rather than a hidden CLI/runtime tweak.

Reset states have the following meanings:

- `fresh`: create a new or empty state;
- `baseline`: restore a predefined experimental baseline;
- `preserve`: carry the preceding state forward.

At least one termination bound must be present.

## 3. Identity and reference rules

The following identifiers are contract-wide within their own namespaces:

- subject IDs;
- requirement IDs;
- check IDs;
- scenario IDs.

Opportunity IDs are unique only within their containing scenario.

Symbolic identifiers use the grammar:

`^[a-z][a-z0-9_]*$`

Every opportunity's `requirement_id` and `check_id` must resolve to exactly one declared
requirement and check respectively.

Broken references are validation errors.

Unused requirements or checks are permitted during development but should produce
validator warnings.

## 4. Validation layers

Validation is intentionally split into layers.

### Layer 1: YAML parsing

The authoring file must parse successfully as YAML.

### Layer 2: JSON Schema validation

The parsed data must satisfy `contract.schema.json`, including required fields, known
field names, controlled values, numeric bounds, and structural constraints.

### Layer 3: ACMM semantic validation

The ACMM validator checks cross-object rules that are clearer to implement in application
logic, including ID uniqueness and reference resolution.

### Layer 4: Harness compatibility validation

Before execution, the harness verifies that symbolic extension points can actually be
resolved, such as evaluator implementations, environment fixtures, tools, and supported
evidence channels.

Only a Contract passing all required pre-execution validation layers is ready to run.

## 5. Contract boundary

The Contract defines experiment intent and execution discipline.

Execution creates run records. Run records contain realized configuration, provenance,
captured evidence, validity/failure information, and atomic opportunity judgments.

Quantification consumes those run records and computes ACMM compliance proportions,
dimension scores, uncertainty, and other derived statistics.

The directional boundary is therefore:

Experiment Contract -> Harness Execution -> Run Record -> Quantification

## 6. v0.1 exclusions

The following ideas are intentionally outside Contract v0.1:

- dynamic opportunity generation;
- scenario generation;
- check composition graphs;
- campaign scheduling;
- subject matrices;
- automatic requirement discovery;
- dynamic opportunity discovery;
- cross-run dependencies;
- ACMM weights and score aggregation;
- confidence scores on checks;
- derived scenario-level outcomes;
- derived run-level outcomes.

These may be reconsidered in later schema versions only when justified by actual
experimental needs.
