# Operator Documentation Conventions

ACMM documentation uses neutral placeholders so command examples do not depend on one researcher's machine layout or local account names.

## Filesystem placeholders

- `<core-repository>` — local checkout of ACMM Core.
- `<experiments-repository>` — local checkout of the Experiment Package repository.
- `<package-directory>` — source directory for one Experiment Package.
- `<virtual-environment>` — Python virtual environment used for ACMM.
- `<secret-file>` — owner-controlled local file containing exactly one credential value. Keep it outside all Git repositories.
- `<generated-root>` — root for generated ACMM artifacts. The default is `~/acmm_generated`; set `ACMM_GENERATED_ROOT` to redirect the complete generated-artifact tree.
- `<execution-directory>` — one generated immediate execution directory.
- `<campaign-directory>` — generated `campaign_<id>` directory.
- `<merge-directory>` — generated `merge_<id>` directory inside a Campaign.
- `<provider-pressure-root>` — generated Provider Pressure store/export source directory.
- `<export-directory>` — deterministic export produced by `acmm export`.
- `<analysis-plan>` — versioned analysis-plan YAML file.

The repositories do not need to be siblings and do not require symbolic links. When execution occurs in WSL but a checkout lives on a Windows-mounted filesystem, keeping the Python environment and mutable runtime data on the Linux filesystem is generally preferable.

## Identity placeholders

Identifiers such as `<experiment-selector>`, `<campaign-id>`, `<adjudication-id>`, `<subject-id>`, and `<scenario-id>` are ACMM identities rather than filesystem paths.

An installed Experiment Package can be selected by experiment ID when only one revision is installed. When multiple immutable revisions of the same experiment ID are installed, use the exact `EXPERIMENT_ID@SHA_PREFIX` form accepted by the CLI.

## Command examples

Examples intentionally omit machine-specific usernames, absolute checkout paths, and credential values. Replace placeholders with local values only at execution time; do not commit those resolved values back into public documentation.

CLI help is authoritative for command-line syntax:

```text
acmm --help
acmm <command> --help
```

Subsystem documentation explains semantic behavior that is intentionally too detailed for the root README.
