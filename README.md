# ACMM

Agent Compliance Metric Model research and experiment infrastructure.

## Current implementation

The repository currently contains Experiment Contract v0.1, including:

- a Draft 2020-12 JSON Schema;
- a YAML authoring template;
- a semantic specification;
- a populated magic-number example;
- YAML loading and Contract validation;
- regression fixtures and tests.

## Development setup

Install the package with test dependencies:

```text
python -m pip install -e ".[test]"
```

Run the test suite:

```text
pytest
```

The current validator performs structural JSON Schema validation followed by ACMM semantic
validation for identifier uniqueness, reference resolution, and unused-definition warnings.
Harness compatibility validation will be added separately.
