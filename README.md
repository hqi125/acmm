# ACMM

ACMM (Agent Compliance Metric Model) is a research framework for running controlled agent experiments, preserving reproducible evidence, adjudicating outcomes, and producing deterministic downstream analysis artifacts.

This repository contains **ACMM Core**: the command-line interface, experiment-independent subject infrastructure, runtime orchestration, persistence, validation, export, and generic analysis tooling. Research experiments live in a separate Experiment Package repository and are installed into Core as immutable package revisions.

## Repository roles

- **ACMM Core** — this repository. Owns infrastructure, runtime, persistence, validation, export, and generic analysis.
- **Experiment Packages** — a separate repository containing Contracts, experiment runtime code, integrations, assets, adjudication code, and reproduction instructions for individual studies.
- **External integration repositories** — optional dependencies used by particular Experiment Packages. They are relevant only when a package explicitly requires them.

For subsystem documentation, start with [`docs/README.md`](docs/README.md).

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

Exact subject images, digests, baseline settings, and LLM definitions are versioned under `infrastructure/`. This table is a reproducibility reference rather than a requirement that every future use of Core run on the same host OS or Python patch release.

## First-time setup

The examples below use neutral placeholders such as `<core-repository>`, `<package-directory>`, and `<experiment-selector>`. See [`docs/operator_conventions.md`](docs/operator_conventions.md) for the complete notation and generated-artifact layout used throughout the documentation.

### 1. Create the Python environment

From a local Core checkout:

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

### 2. Install the R dependencies

The generic analysis layer uses base R plus three CRAN packages:

```text
Rscript -e 'install.packages(c("digest", "jsonlite", "yaml"))'
Rscript analysis/r/tests/run_tests.R
```

### 3. Start Docker and reconcile the subject environment

Start Docker Desktop with WSL integration enabled, then select a registered LLM baseline:

```text
acmm env set-llm <llm-id>
acmm env status
acmm env doctor --start-if-needed
```

For reproduction of the frozen GPT-OSS study baseline, `<llm-id>` is `gpt_oss_120b_cloud_v1`.

Normal research workflows use `acmm env`. The lower-level `acmm-infra` command family is intended for subject-specific diagnostics and integration work; see [`infrastructure/README.md`](infrastructure/README.md).

### 4. Initialize local secret bindings

Secrets are local operator configuration and must never be committed to Core or an Experiment Package repository.

```text
acmm secret init
acmm secret validate
```

Bind only the credentials required by the selected workflow. For example, Ollama Cloud usage telemetry uses the Core-scoped binding below:

```text
acmm secret bind core ollama_api_key --file <secret-file>
```

Experiment- and integration-scoped credentials are declared by the installed package and are bound through the corresponding `acmm secret bind` scope. See [`docs/security/secrets.md`](docs/security/secrets.md).

### 5. Validate and install an Experiment Package

```text
acmm package validate <package-directory>
acmm package install <package-directory>
acmm package list
```

A package is installed as an immutable content-addressed revision. Use the experiment ID when only one revision is installed, or `EXPERIMENT_ID@SHA_PREFIX` to select an exact revision.

Before real execution, verify the environment and preview the selected work:

```text
acmm env doctor --start-if-needed
acmm run <experiment-selector> --preview
```

## Choosing an execution path

ACMM has two scientific execution entry points that share the same underlying runtime machinery but serve different operational needs.

| Entry point | Use it when |
| --- | --- |
| `acmm run` | You want an immediate execution session or a selected subset and do not need durable Campaign lifecycle management. |
| `acmm campaign` | You need a frozen logical population that can span multiple sessions, resume incomplete work, support Campaign-level adjudication, or produce an immutable merged study artifact. |

For final multi-session acquisition, Campaigns are usually the appropriate durable surface. For qualification, focused reruns, or small selected executions, `acmm run` is simpler.

## Immediate execution with `acmm run`

Preview before executing:

```text
acmm run <experiment-selector> --preview
```

Selectors can be combined:

```text
acmm run <experiment-selector> \
  --subject <subject-id> \
  --scenario <scenario-id> \
  --repetition 1 \
  --preview
```

Execute with explicit reconciliation/start permissions when required:

```text
acmm run <experiment-selector> \
  --apply-if-needed \
  --start-if-needed \
  --capture-runtime-logs
```

Immediate results are written below `<generated-root>/results/`. Selection, retry, persistence, interruption, and diagnostic semantics are documented under [`docs/runtime/`](docs/runtime/).

## Durable execution with `acmm campaign`

Preview and create a Campaign from one installed package revision:

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

Inspect or resume durable state:

```text
acmm campaign status <campaign-id>
acmm campaign resume <campaign-id> --apply-if-needed --start-if-needed
```

A complete Campaign can be re-adjudicated by a compatible installed package and then merged:

```text
acmm campaign adjudicate <campaign-id> --package <adjudicator-selector>
acmm campaign merge <campaign-id> --adjudication <adjudication-id>
```

An intentionally incomplete Campaign can instead be materialized as a visibly partial merge with `--allow-partial`.

See [`docs/runtime/campaign.md`](docs/runtime/campaign.md) for Campaign state, resumption, adjudication, merge, and compatibility rules.

## Export and analysis

Export is mechanical and deterministic. Statistical analysis is downstream and does not mutate canonical acquisition artifacts.

```text
acmm export run <execution-directory>
acmm export campaign <campaign-directory> <merge-directory>
acmm export provider-pressure <provider-pressure-root>
acmm analyze <export-directory> --plan <analysis-plan>
```

The preferred Campaign analysis plan is `analysis/plans/standard_v0_4.yaml`; the preferred Provider Pressure plan is `analysis/plans/provider_pressure_v0_3.yaml`.

See [`docs/analysis/export.md`](docs/analysis/export.md), [`docs/analysis/plan.md`](docs/analysis/plan.md), and [`analysis/r/README.md`](analysis/r/README.md).

## Routine package and environment maintenance

Common inspection and maintenance commands are:

```text
acmm package list
acmm package show <experiment-selector>
acmm env status
acmm env doctor --start-if-needed
acmm env stop
```

When package source changes and an older revision of the same experiment is already installed, use the explicit convergence path:

```text
acmm package validate <package-directory>
acmm package install <package-directory> --upgrade
```

Package application, detach/uninstall/purge behavior, and package-defined engineering apparatus commands are documented in [`docs/experiments/package.md`](docs/experiments/package.md). The complete CLI family map is in [`docs/runtime/cli.md`](docs/runtime/cli.md).

## Security and publication

- Never commit live API keys, PATs, passwords, private keys, secret-bearing `.env` files, or local secret registries.
- Keep secret source files outside the repositories and bind them through `acmm secret`.
- Generated results and mutable runtime state should not be committed unless a study deliberately publishes a curated artifact separately.
- Before publishing, inspect both the current tree and the repository's Git history for credentials or personal paths. Removing a secret from the latest commit does not remove it from history.

See [`docs/security/secrets.md`](docs/security/secrets.md).

## Documentation map

[`docs/README.md`](docs/README.md) is the documentation index. Useful starting points include:

- [`docs/operator_conventions.md`](docs/operator_conventions.md) — placeholders, selectors, and generated-root conventions used by examples.
- [`docs/experiments/package.md`](docs/experiments/package.md) — Experiment Package lifecycle and ownership.
- [`docs/runtime/campaign.md`](docs/runtime/campaign.md) — durable Campaign execution, resume, adjudication, and merge.
- [`docs/analysis/export.md`](docs/analysis/export.md) — deterministic run, Campaign, and Provider Pressure exports.
- [`docs/analysis/plan.md`](docs/analysis/plan.md) — versioned generic analysis orchestration.
- [`docs/security/secrets.md`](docs/security/secrets.md) — local secret handling and public-release safeguards.
- [`infrastructure/README.md`](infrastructure/README.md) — experiment-independent subject infrastructure.

Contract and Run Record semantic specifications remain under `docs/contract/` and `docs/record/`.
