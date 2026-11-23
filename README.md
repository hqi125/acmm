# ACMM

ACMM (Agent Compliance Metric Model) is a research framework for running controlled agent experiments, preserving reproducible evidence, adjudicating outcomes, and producing deterministic analysis exports.

This repository contains ACMM Core: the command-line interface, experiment-independent subject infrastructure, runtime orchestration, persistence, validation, export, and generic analysis tooling. Research experiments live in a separate Experiment Package repository and are installed into Core as immutable package revisions.

## Repository roles

- **ACMM Core** — this repository. Owns infrastructure, runtime, persistence, validation, export, and generic analysis.
- **Experiment Packages** — a separate repository containing Contracts, experiment runtime code, integrations, assets, adjudication code, and reproduction instructions for individual studies.
- **External integration repositories** — optional engineering dependencies used by particular Experiment Packages. They are not part of the Core public workflow unless the package requires them.

For detailed documentation, start with [`docs/README.md`](docs/README.md).

## Reference environment

The frozen research environment used for the current ACMM studies was developed and validated with:

| Component | Reference environment |
| --- | --- |
| Host OS | Windows 11 |
| Linux environment | WSL2 with Ubuntu |
| Container runtime | Docker Desktop with WSL integration |
| Python used for the frozen study | 3.14.8 |
| Python supported by Core | 3.11 or newer |
| R used for the frozen analysis | 4.3.3 |
| Hermes Agent | 0.21.3, image `nousresearch/hermes-agent:v2026.9.14` |
| Open WebUI | 0.11.4 |
| Agent Zero | v2.14 |
| Registered study LLM baseline | `gpt_oss_120b_cloud_v1` |

Exact subject images, digests, baseline settings, and LLM definitions are versioned under `infrastructure/`. The table above is a reproducibility reference, not a claim that every future Core use must run on the same host OS or Python patch release.

## Path placeholders used in this documentation

Commands use placeholders instead of personal or machine-specific paths:

- `<core-repository>` — local checkout of this repository.
- `<experiments-repository>` — local checkout of the Experiment Package repository.
- `<package-directory>` — one Experiment Package source directory.
- `<virtual-environment>` — Python virtual environment used for ACMM.
- `<secret-file>` — local file containing one credential. Keep it outside all Git repositories.
- `<generated-root>` — root directory for generated ACMM artifacts. The default is `~/acmm_generated`; set `ACMM_GENERATED_ROOT` to redirect it.
- `<campaign-directory>` — generated `campaign_<id>` directory.
- `<merge-directory>` — generated `merge_<id>` directory inside a Campaign.
- `<export-directory>` — deterministic export produced by `acmm export`.
- `<analysis-plan>` — versioned analysis-plan YAML file.

Identifiers such as `<experiment-selector>`, `<campaign-id>`, `<subject-id>`, and `<scenario-id>` are ACMM identities rather than filesystem paths.

## First-time setup

### 1. Clone the repositories

Clone Core and the Experiment Package repository into paths of your choice:

```text
git clone <core-repository-url> <core-repository>
git clone <experiments-repository-url> <experiments-repository>
```

The repositories do not need to be siblings and do not require symbolic links. If the checkouts live on a Windows-mounted filesystem while execution happens in WSL, keep the Python virtual environment and mutable runtime data on the Linux filesystem when possible.

### 2. Create the Python environment

From the Core checkout:

```text
cd <core-repository>
python -m venv <virtual-environment>
source <virtual-environment>/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

Run the Core test suite:

```text
pytest
```

The project metadata accepts Python 3.11 or newer. Python 3.14.8 is the frozen study reference.

### 3. Install the R dependencies

The generic analysis layer uses base R plus three CRAN packages:

```text
Rscript -e 'install.packages(c("digest", "jsonlite", "yaml"))'
```

Verify the R analysis tests:

```text
Rscript analysis/r/tests/run_tests.R
```

### 4. Start Docker and reconcile the subject environment

Start Docker Desktop with WSL integration enabled, then select a registered LLM baseline:

```text
acmm env set-llm <llm-id>
acmm env status
acmm env doctor --start-if-needed
```

For reproduction of the frozen GPT-OSS study baseline, `<llm-id>` is `gpt_oss_120b_cloud_v1`.

Normal research workflows use `acmm env`. The lower-level `acmm-infra` command family is intended for subject-specific infrastructure diagnosis and integration work; see [`infrastructure/README.md`](infrastructure/README.md).

### 5. Initialize local secret bindings

Secrets are local operator configuration and must never be committed to either repository.

```text
acmm secret init
acmm secret bind core ollama_api_key --file <secret-file>
acmm secret validate
```

`<secret-file>` is a local owner-controlled file containing the credential value. Package-specific experiment or integration secrets are bound only when the selected package declares them:

```text
acmm secret bind experiment <experiment-id> <secret-id> --file <secret-file>
acmm secret bind integration <experiment-id> <integration-id> <secret-id> --file <secret-file>
```

See [`docs/security/secrets.md`](docs/security/secrets.md) for binding scopes, registry upgrades, and publication safeguards.

### 6. Validate and install an Experiment Package

```text
acmm package validate <package-directory>
acmm package install <package-directory>
acmm package list
```

A package is installed as an immutable content-addressed revision. Use the experiment ID when only one revision is installed, or `EXPERIMENT_ID@SHA_PREFIX` to select an exact revision.

Before real execution, verify the environment again:

```text
acmm env doctor --start-if-needed
acmm run <experiment-selector> --preview
```

## Day-to-day workflow

A normal session is intentionally short:

```text
cd <core-repository>
source <virtual-environment>/bin/activate
acmm env doctor --start-if-needed
acmm package list
```

When package source changes and an older revision of the same experiment is already installed, converge explicitly:

```text
acmm package validate <package-directory>
acmm package install <package-directory> --upgrade
```

Package application can be done explicitly:

```text
acmm package apply <experiment-selector>
```

or granted to an execution through `--apply-if-needed`. Starting a correctly deployed but stopped subject is a separate permission, `--start-if-needed`.

## Execution entry point 1: `acmm run`

`acmm run` is the immediate execution path. Use it for one execution session or a selected subset that does not need durable Campaign lifecycle management.

Preview first:

```text
acmm run <experiment-selector> --preview
```

Selectors can be combined when needed:

```text
acmm run <experiment-selector> \
  --subject <subject-id> \
  --scenario <scenario-id> \
  --repetition 1 \
  --preview
```

Execute with explicit reconciliation/start permissions:

```text
acmm run <experiment-selector> \
  --apply-if-needed \
  --start-if-needed \
  --capture-runtime-logs
```

Immediate results are written below `<generated-root>/results/`. Set `ACMM_GENERATED_ROOT` before the command to redirect the complete generated-artifact tree for a session or study.

Detailed selection, retry, persistence, and interruption semantics are documented under [`docs/runtime/`](docs/runtime/).

## Execution entry point 2: `acmm campaign`

`acmm campaign` freezes one installed package revision, its Contract, materialization policy, and complete logical run population into a durable study artifact. Use Campaigns when work may span multiple sessions, needs explicit resumption, or requires Campaign-level adjudication and merge products.

Preview and create:

```text
acmm campaign create <experiment-selector> --preview
acmm campaign create <experiment-selector>
```

Preview a Campaign selection without executing it:

```text
acmm campaign execute <campaign-id> \
  --scenario <scenario-id> \
  --repetition 1 \
  --preview
```

Execute selected pending work:

```text
acmm campaign execute <campaign-id> \
  --scenario <scenario-id> \
  --repetition 1 \
  --apply-if-needed \
  --start-if-needed \
  --capture-runtime-logs
```

Inspect or resume:

```text
acmm campaign status <campaign-id>
acmm campaign resume <campaign-id> --apply-if-needed --start-if-needed
```

A complete Campaign can be adjudicated by an installed package that provides compatible adjudication code:

```text
acmm campaign adjudicate <campaign-id> --package <adjudicator-selector>
```

Create the authoritative merge, optionally applying that adjudication:

```text
acmm campaign merge <campaign-id> --adjudication <adjudication-id>
```

An explicitly incomplete Campaign can instead be materialized without adjudication as a visibly partial merge:

```text
acmm campaign merge <campaign-id> --allow-partial
```

Campaign adjudication is intentionally a whole-Campaign operation and requires the complete logical population. See [`docs/runtime/campaign.md`](docs/runtime/campaign.md).

## Export and analysis

Export is mechanical and deterministic. Statistical analysis is downstream and does not mutate canonical acquisition artifacts.

Immediate execution:

```text
acmm export run <execution-directory>
```

Campaign:

```text
acmm export campaign <campaign-directory> <merge-directory>
```

Provider Pressure telemetry:

```text
acmm export provider-pressure <provider-pressure-root>
```

Analyze an export with an explicit plan:

```text
acmm analyze <export-directory> --plan <analysis-plan>
```

The preferred Campaign analysis plan is `analysis/plans/standard_v0_4.yaml`. The preferred Provider Pressure plan is `analysis/plans/provider_pressure_v0_3.yaml`.

See [`docs/analysis/export.md`](docs/analysis/export.md), [`docs/analysis/plan.md`](docs/analysis/plan.md), and [`analysis/r/README.md`](analysis/r/README.md).

## Package and environment maintenance

Useful package commands:

```text
acmm package list
acmm package show <experiment-selector>
acmm package detach <experiment-selector>
acmm package uninstall <experiment-selector>
acmm package purge
```

Useful environment commands:

```text
acmm env status
acmm env doctor --start-if-needed
acmm env stop
```

Package-defined engineering apparatus operations are intentionally separate from scientific run lifecycle hooks:

```text
acmm run apparatus list
acmm run apparatus exec <action>
acmm run apparatus cleanup --execution <execution-directory> --preview
```

See [`docs/experiments/package.md`](docs/experiments/package.md) for package lifecycle details.

## Security and publication

- Never commit live API keys, PATs, passwords, private keys, secret-bearing `.env` files, or local secret registries.
- Keep secret source files outside the repositories and bind them through `acmm secret`.
- Generated results and local runtime state should not be committed unless a study deliberately publishes a curated artifact separately.
- Before publishing a repository, inspect the current tree **and Git history** for credentials or personal paths. Removing a secret from the latest commit does not remove it from history.

See [`docs/security/secrets.md`](docs/security/secrets.md).

## Documentation map

[`docs/README.md`](docs/README.md) is the documentation index. The most useful starting points are:

- [`docs/experiments/package.md`](docs/experiments/package.md) — Experiment Package lifecycle and ownership.
- [`docs/runtime/campaign.md`](docs/runtime/campaign.md) — durable Campaign execution, resume, adjudication, and merge.
- [`docs/analysis/export.md`](docs/analysis/export.md) — deterministic run, Campaign, and Provider Pressure exports.
- [`docs/analysis/plan.md`](docs/analysis/plan.md) — versioned generic analysis orchestration.
- [`docs/security/secrets.md`](docs/security/secrets.md) — local secret handling and public-release safeguards.
- [`infrastructure/README.md`](infrastructure/README.md) — experiment-independent subject infrastructure.

Contract and Run Record semantic specifications remain under `docs/contract/` and `docs/record/`.
