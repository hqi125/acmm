# ACMM

Agent Compliance Metric Model research and experiment infrastructure.

## Current implementation

The repository currently contains Experiment Contract v0.1, including:

- a Draft 2020-12 JSON Schema;
- a YAML authoring template;
- a semantic specification;
- neutral Contract examples and regression fixtures;
- YAML loading and Contract validation;
- harness-compatibility validation;
- deterministic runtime planning;
- platform-neutral single-run execution orchestration;
- experiment-level plan execution with explicit invalid-attempt retries;
- a concrete Hermes Agent adapter using structured JSONL events;
- qualified Hermes, Open WebUI, and Agent Zero adapters with package-owned runtime fixtures/evaluators;
- reproducible Hermes infrastructure management through `acmm-infra`;
- atomic per-run result persistence;
- ACMM Run Record v0.1 schema and semantic validation;
- regression fixtures and tests;
- installed Experiment Package validation, installation, deployment, and execution commands;
- the separate `acmm-infra` deployment/inspection command.

The CLI ownership boundary is summarized in `docs/runtime/cli.md`.

The Experiment Package architecture is specified in `docs/experiments/package.md`. Experiment
Packages are now the sole researcher-facing experiment identity: raw Experiment Contracts remain
package components and internal validation artifacts rather than standalone executable experiments.

## Development environment

The current development layout keeps the Git repository on the Windows side while running
the ACMM Python environment and experiment infrastructure inside WSL.

A typical layout is:

```text
Windows
└── ACMM Git repository
    └── visible in WSL through /mnt/c/...

WSL
├── ~/acmm_repo
│   └── symbolic link to the Windows Git repository
├── ~/acmm_runtime
│   └── mutable deployed agent infrastructure
├── ~/.pyenv
│   └── managed Python installations
└── ~/.venvs/acmm_repo
    └── ACMM-specific virtual environment
```

This keeps source control and editing convenient on Windows while keeping Python
interpreters, installed packages, and runtime tooling on the native WSL filesystem.

### 1. Install build dependencies in WSL

Pyenv builds CPython from source, so install the standard Ubuntu/Debian build
dependencies first:

```text
sudo apt update

sudo apt install -y \
    git \
    build-essential \
    libssl-dev \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    libncursesw5-dev \
    xz-utils \
    tk-dev \
    libxml2-dev \
    libxmlsec1-dev \
    libffi-dev \
    liblzma-dev \
    libzstd-dev \
    curl \
    wget
```

### 2. Install pyenv from Git

This project uses the Git-checkout installation of pyenv because it makes updating pyenv
straightforward.

```text
git clone https://github.com/pyenv/pyenv.git ~/.pyenv
```

For Bash, add the following initialization lines to both `~/.bashrc` and the login profile
used by the WSL distribution, typically `~/.profile`:

```text
export PYENV_ROOT="$HOME/.pyenv"
[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init - bash)"
```

Restart the shell after changing the startup files:

```text
exec "$SHELL"
```

Verify the installation:

```text
pyenv --version
pyenv root
```

### 3. Update pyenv

Because pyenv is installed as a Git checkout, update it with:

```text
cd "$(pyenv root)"
git pull
```

Then restart the shell if needed:

```text
exec "$SHELL"
```

Updating pyenv also refreshes the Python build definitions available through
`pyenv install`.

Useful checks after an update are:

```text
pyenv --version
pyenv install --list
pyenv install --list | grep '  3\.14\.'
```

### 4. Install the ACMM Python version

The current ACMM development baseline is Python 3.14.8:

```text
pyenv install 3.14.8
```

List installed interpreters:

```text
pyenv versions
```

Select Python 3.14.8 for the current shell:

```text
pyenv shell 3.14.8
```

Verify the selected interpreter:

```text
python --version
which python
pyenv version
```

The ACMM workflow intentionally uses `pyenv shell` rather than requiring a committed
`.python-version` file in the Windows-backed repository. The virtual environment created
below permanently records the interpreter from which it was created.

### 5. Expose the Windows repository inside WSL

WSL automatically exposes Windows drives under `/mnt`. A symbolic link can give the
repository a convenient WSL-side name without copying it.

For example:

```text
ln -s /mnt/c/path/to/acmm ~/acmm_repo
```

When the separate Experiment Package repository is also checked out on Windows, give it a stable
WSL-side name as well:

```text
ln -s /mnt/c/path/to/acmm-experiments ~/acmm_experiments
```

Then:

```text
cd ~/acmm_repo
```

Changes made on Windows are immediately visible through this path, and changes made from
WSL update the same working tree.

The symbolic link is only a convenient alias; no separate repository copy is created.

### 6. Create the ACMM virtual environment

Keep the virtual environment on the native WSL filesystem rather than inside the
Windows-backed repository:

```text
mkdir -p ~/.venvs

pyenv shell 3.14.8
python -m venv ~/.venvs/acmm_repo
```

Activate it:

```text
source ~/.venvs/acmm_repo/bin/activate
```

Verify the environment:

```text
python --version
which python
```

A correct setup should report Python 3.14.8 and a Python executable under:

```text
~/.venvs/acmm_repo/bin/python
```

A virtual environment isolates the packages and command-line tools installed for ACMM
from other Python projects and from the underlying pyenv installation.

To leave the virtual environment:

```text
deactivate
```

After deactivation, `python` once again resolves according to the surrounding pyenv/shell
configuration.

### 7. Install ACMM in editable mode

With the virtual environment active:

```text
cd ~/acmm_repo
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

The `-e` editable installation points the installed ACMM package back to the repository
source tree. Source changes made on Windows or through the WSL symbolic link are therefore
used on the next Python invocation without reinstalling the package.

Editable installation does not copy and repack the ACMM source code every time the virtual
environment is activated. The installation creates the package metadata, installs declared
dependencies and command-line entry points into the virtual environment, and records an
editable reference/import mechanism back to the working source tree.

As a result:

- ordinary edits to existing Python source files are visible on the next Python process;
- activating or deactivating the virtual environment does not rebuild ACMM;
- changing package metadata in `pyproject.toml`, dependencies, or command-line entry points
  should be followed by another `python -m pip install -e ".[test]"`;
- substantial package-layout/discovery changes should also be followed by reinstalling the
  editable package.

Setuptools may generate a directory such as `src/acmm.egg-info/` during editable
installation. This directory contains generated distribution metadata; it is not source
code and must not be committed. The repository `.gitignore` excludes `*.egg-info/` along
with other Python build artifacts such as `build/` and `dist/`.

### 8. Run the test suite

From the repository root with the virtual environment active:

```text
pytest
```

The current baseline test suite should pass completely.

### 9. Validate, install, and preview an experiment

Experiment Packages are the researcher-facing experiment unit. Research Contracts, runtime code,
and package-local capability integrations live in the sibling experiments workspace rather than
inside Core. Validate package source without installing or executing runtime code:

```text
acmm package validate <package-directory>
```

Install the immutable package snapshot into the ACMM-managed store:

```text
acmm package install <package-directory>
```

Then inspect the deterministic run selection without executing an agent:

```text
acmm run <experiment-selector> --preview
```

`<experiment-selector>` is the experiment ID when one revision is installed, or
`EXPERIMENT_ID@SHA_PREFIX` when an exact installed revision must be selected. Package and Contract
validation failures return a nonzero exit status.

Inspect the most recent command's exit status with:

```text
echo $?
```

## Daily WSL workflow

Once the environment has been created, a normal development session does not need to
recreate it or reinstall ACMM.

```text
cd ~/acmm_repo
source ~/.venvs/acmm_repo/bin/activate

pytest
acmm package list
```

When finished:

```text
deactivate
```

Because ACMM is installed in editable mode, ordinary source edits do not require another
`pip install`.

## Useful pyenv commands

```text
pyenv --version
```

Show the installed pyenv version.

```text
pyenv root
```

Show the pyenv installation directory.

```text
pyenv versions
```

List installed Python versions.

```text
pyenv install --list
```

List Python versions that the current pyenv/python-build installation knows how to build.

```text
pyenv install 3.14.8
```

Install a specific Python version.

```text
pyenv shell 3.14.8
```

Select a Python version for the current shell session.

```text
pyenv version
```

Show the currently selected Python version and why it was selected.

```text
pyenv which python
```

Show the real Python executable selected through pyenv.

```text
pyenv uninstall <version>
```

Remove a Python version managed by pyenv.

## Recreating the virtual environment

If the ACMM virtual environment ever needs to be rebuilt:

```text
deactivate
rm -rf ~/.venvs/acmm_repo

pyenv shell 3.14.8
python -m venv ~/.venvs/acmm_repo
source ~/.venvs/acmm_repo/bin/activate

cd ~/acmm_repo
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
pytest
```

Do not remove `~/.venvs/acmm_repo` while it is active.

## Contract validation architecture

Contract validation performs JSON Schema validation followed by ACMM semantic validation
for identifier uniqueness, reference resolution, and unused-definition warnings.

Harness compatibility is a separate validation layer. It receives the capabilities of a
concrete harness and checks evaluator, fixture, tool, and evidence-channel availability.
This separation keeps the Contract validator independent of a particular agent platform.

## Runtime planning

The researcher-facing planning path operates on an installed Experiment Package:

```text
acmm run <experiment-selector> --preview
```

Package loading validates the embedded Contract before runtime planning. Preview prints the
resolved deterministic run selection without creating an execution session or contacting agents.
Validation errors block planning; warnings are reported but do not.

After validation, `build_experiment_plan()` expands a Contract into the deterministic run
population defined by:

```text
subjects x scenarios x repetitions
```

By default, plan ordering follows subject declaration order, then scenario declaration
order, then one-based repetition order. Contracts may set
`run_protocol.ordering: subject_repetition_scenario` to interleave scenario conditions
within each subject repetition while preserving deterministic run indices. Interleaved
plans may additionally set `run_protocol.scenario_ordering: cyclic_rotation` to rotate
scenario declaration order across repetitions and subject starting phases, providing
deterministic scenario-position counterbalancing. Each planned run receives a zero-based
`run_index` for its position in the plan.

The planner assumes the Contract has already passed validation and intentionally does not
repeat schema or semantic checks. See `docs/runtime/planning.md` for the runtime-planning
boundary and ordering rules.

## Run Record v0.1

A Run Record is the execution-side counterpart of an Experiment Contract:

```text
Contract
    = what should be run

Run Record
    = what actually happened in one realized execution
```

The v0.1 package lives under:

```text
records/v0_1/
├── README.md
├── run_record.schema.json
└── run_record.example.yaml
```

The Python implementation lives under `src/acmm/record/`.

Run Record validation is Contract-aware. After JSON Schema validation, the semantic
validator checks:

- binding to the Contract experiment and deterministic planned-run coordinate;
- optional verification of the exact Contract artifact SHA-256;
- execution timestamp ordering;
- evidence ID and evidence-channel uniqueness;
- safe relative evidence artifact paths;
- valid-run evidence completeness against the Contract evidence policy;
- opportunity judgment uniqueness and completeness;
- opportunity and evidence reference resolution.

Execution validity and compliance outcomes are intentionally separate. A valid run may
contain violations and remains scientific evidence. An invalid run represents an execution
that cannot be trusted scientifically and is excluded from ACMM quantification. Invalid
runs may contain zero or partial evidence and judgments.

See `docs/record/semantics.md` for the complete v0.1 semantics.

## Execution orchestration

The runtime can now transform one validated `planned_run` into an in-memory
`executed_run`.

The orchestration layer resolves runtime implementations through an `execution_registry`:

```text
subject_id  -> subject adapter
fixture_id  -> environment fixture
evaluator   -> opportunity evaluator
```

The Experiment Contract remains platform-neutral; the registry supplies executable
runtime dependencies without adding adapter implementation details to the Contract.

`execute_planned_run()` prepares the environment, realizes any fixture-provided non-secret task
bindings, invokes the subject adapter, captures normalized evidence, optionally captures trusted
final environment state, finalizes/restores the fixture, evaluates scenario opportunities, computes
evidence SHA-256 digests, and constructs a Run Record. Fixtures may contribute Contract-declared
`initial_environment_state` and `final_environment_state` channels so authoritative external state
can be adjudicated independently from subject claims.

Infrastructure exceptions are converted into Run Records with
`execution.validity: invalid` rather than being interpreted as compliance violations.

The returned `executed_run` remains in memory and contains both the Run Record and evidence
artifact bytes. Persistent result-directory writing is intentionally the next layer.

See `docs/runtime/execution.md` for the execution boundary and failure semantics.

## Result persistence

Generated artifacts default to `~/acmm_generated`. Set `ACMM_GENERATED_ROOT` to move the entire generated-artifact tree without changing individual commands. Immediate execution results live under the `results/` namespace, analysis exports under `exports/`, and durable campaigns use the same generated root. Explicit command-line output-root options still override these defaults.

`persist_executed_run()` writes an in-memory execution result into:

```text
~/acmm_generated/results/<experiment_id>/run_<run_index>_<run_id>/
```

Each published directory contains `run_record.yaml` and the evidence artifacts referenced
by the record.

Before any filesystem publication, the writer verifies that evidence IDs, channels, paths,
captured bytes, and SHA-256 values agree with the Run Record manifest. Unsafe paths,
missing/extra artifacts, duplicate artifact paths, and attempts to overwrite an existing
run are rejected.

The bundle is written first into a hidden sibling temporary directory and renamed into its
final location only after all writes succeed. A failed write therefore does not leave a
partially written directory that looks like a completed run.

See `docs/runtime/persistence.md` for the exact storage layout and durability boundary.

## Experiment-level execution

`execute_experiment()` stores one execution session under:

```text
~/acmm_generated/results/<experiment_id>/execution_<execution_id>/
```

The session snapshots the exact Contract bytes before running any selected planned slots. By
default the complete deterministic plan is selected. Engineering or scoped executions may select
explicit run indices; the manifest records both the full Contract run count and the selected
indices so a subset cannot be mistaken for a complete Contract execution.

Every realized attempt is preserved. An attempt is retried only when
`execution.validity` is `invalid`; the first valid attempt resolves the planned slot.
Valid agent outcomes such as `timeout` or `max_steps` are not retried.

Contracts may additionally specify a fixed `run_protocol.pacing.inter_run_delay_seconds`.
The executor applies it between selected planned slots, not between retries and not after
the final slot, and records the configured pacing in the completion manifest. The configured delay is experiment-defined and is recorded as execution provenance rather than
being inferred by Core.

The session completion manifest, `experiment_execution.yaml`, is written only after the
selected plan has been traversed. If orchestration or persistence is interrupted, previously
published Run Records remain intact but the session has no completion manifest and cannot
be mistaken for a completed collection.

See `docs/runtime/experiment_execution.md` for the acquisition and retry semantics.

## Analysis-ready CSV export

A completed execution can now be projected into deterministic downstream analysis tables:

```text
acmm export ~/acmm_generated/results/<experiment_id>/execution_<execution_id>
```

The default output is separate from canonical results:

```text
~/acmm_generated/exports/<experiment_id>/execution_<execution_id>/
├── runs.csv
├── judgments.csv
└── export_manifest.yaml
```

`runs.csv` preserves every realized attempt, including invalid retries, and marks the selected
scientifically valid attempt explicitly. `judgments.csv` preserves recorded opportunity outcomes
and joins requirement/dimension/check metadata from the Contract. No ACMM estimators or scores are
computed by the exporter.

Before publishing, export verifies the Contract snapshot hash, manifest-to-Run-Record bindings,
Run Record validity, and persisted evidence SHA-256 values. Existing export directories are not
overwritten implicitly.

See `docs/analysis/export.md`.

## Statistical-analysis boundary

The experiment automation intentionally stops before statistical interpretation.

Canonical Contracts, Run Records, execution manifests, and evidence remain the source of
truth. `acmm export` projects validated experimental facts into CSV tables for the separate
`analysis/r/` statistical workspace.

The R workspace validates export manifests/hashes and reconstructs the selected valid
population before computing opportunity-, run-, and scenario-weighted compliance rates. The default
`strict` population policy requires every selected planned slot to resolve. An explicit
`available-valid` policy supports postmortem/sensitivity analysis of imperfect executions while
keeping acquisition coverage and exact missing planned slots visible; invalid harness attempts are
missing observations, not compliance failures. A separate general availability layer now formalizes
planned-slot acquisition, first-attempt/retry behavior, scenario/position imbalance, hierarchical
availability intervals, and explicit missing-outcome sensitivity envelopes.
Requirement-level grouping is the default. The current analysis layer also implements
scenario-to-run hierarchical bootstrap intervals, explicit dimension-level `S_D` scores with
arithmetic, geometric, harmonic, and minimum aggregation, and hierarchical uncertainty propagated
through those dimension scores. Hypothesis tests, statistical models, plots, and custom requirement
weights remain later analysis stages.

Run the R regression suite with:

```text
Rscript analysis/r/tests/run_tests.R
```

For routine end-to-end processing of one completed execution, use:

```text
python tools/analyze_execution.py \
    ~/acmm_generated/results/<experiment_id>/execution_<execution_id>
```

The wrapper performs or reuses the deterministic CSV export, then resolves the compliance
population automatically. Complete acquisitions use the strict population. Incomplete acquisitions
produce a non-blocking warning and fall back to the available-valid population; unrelated analysis
errors still stop the workflow. Requirement point estimates, hierarchical uncertainty, dimension
scores, and dimension hierarchical uncertainty then run under that resolved population. The general
availability analysis runs for every structurally valid execution and remains a separate measurement
layer.

Human-readable workbooks stay directly under `analysis_output/`, R-generated figures stay under
`analysis_output/figures/`, derived CSV tables are organized under `analysis_output/data/`, and text
diagnostics are organized under `analysis_output/logs/`. `runs.csv` and `judgments.csv` remain the
canonical normalized export tables one level above. The individual R scripts remain available for
interactive R/RStudio work; see `analysis/r/README.md`.

See `docs/analysis/boundary.md`, `docs/analysis/export.md`, `docs/analysis/availability.md`, and
`analysis/r/README.md`.

## Experiment Package workflow

Research experiments are external Experiment Packages. Core does not contain a built-in experiment
catalog, experiment Contracts, pilot runtime modules, or concrete research-tool integrations.
A typical two-repository workspace is:

```text
~/acmm_repo/          ACMM Core, infrastructure, adapters, analysis, and CLI
~/acmm_experiments/   independently versioned Experiment Packages
~/acmm_runtime/       mutable deployments and installed package snapshots
```

A package is validated and installed before ACMM can execute it:

```text
acmm package validate ~/acmm_experiments/<experiment-id>
acmm package install ~/acmm_experiments/<experiment-id>
acmm package list
acmm package show <experiment-id>
```

`acmm package apply <selector>` prepares the complete package cohort. `acmm run` remains strict by
default and requires an explicit installed-package selector:

```text
acmm package apply <experiment-id>
acmm run apparatus list                # list package-owned engineering apparatus operations
acmm run apparatus exec <action>       # invoke one free-form package action
acmm run apparatus cleanup --execution <execution-dir>
                                       # clean package-owned quarantined resources
acmm run <experiment-id> --preview
acmm run <experiment-id>
```

For convenience, `--apply-if-needed` grants `run` permission to reconcile only the subjects needed by
the selected run set. ACMM always repeats the strict deployment preflight after reconciliation and
aborts if the persisted package context still does not match:

```text
acmm run <experiment-id> --subject <subject-id> --run-index 0-9 --apply-if-needed
```

`--preview` is non-mutating. When package context diverges it reports whether strict execution would
fail or whether `--apply-if-needed` would permit reconciliation. `--start-if-needed` independently
grants permission to start a correctly deployed but stopped subject runtime. Runtime acquisition is
retried up to `--startup-max-attempts` with progressive cooldowns after transient startup failures.
`--prestart-selected-subjects` can be combined with `--start-if-needed` to acquire and readiness-check
all selected runtimes before the execution session is created. Neither option weakens the final
readiness checks.

Package-owned **engineering apparatus actions** are explicit and never run as a side effect of `apply`,
`doctor`, or package installation. They live under an independent `acmm run apparatus` command family because
they may touch the external experimental world. `acmm run apparatus list` resolves the one fully converged
Experiment Package deployment and lists its package-defined operations; `acmm run apparatus exec <action>`
invokes one without repeating the experiment selector. The bare `acmm run apparatus` command is intentionally
invalid so every apparatus operation has an explicit positional command. Each apparatus command owns its own
arguments: for example, `--preview` under `cleanup` is unrelated to scientific `acmm run <experiment> --preview`.
Long-running actions may publish structured progress events, which the CLI renders as immediate `[step/total]`
status lines. For example:

```text
acmm run apparatus list
acmm run apparatus exec initialize
acmm run apparatus cleanup --execution ~/acmm_generated/results/<experiment-id>/<execution-id> --preview
acmm run apparatus cleanup --execution ~/acmm_generated/results/<experiment-id>/<execution-id>
```

Engineering apparatus actions are intentionally distinct from the typed scientific runtime lifecycle. Packages
may additionally expose typed apparatus cleanup for execution-recorded quarantines; Core supplies the execution
path while the package owns external-system identity checks and deletion semantics. Packages may implement the fixed
lifecycle hooks `session_initialize`, `run_prepare`, `run_observe`, `run_finalize`,
and `session_finalize`, but operators do not invoke those hooks independently. Core owns their ordering. In
particular, `run_finalize` is invoked only after the run record and evidence artifacts have been durably
persisted; a package may therefore perform destructive cleanup there without deleting the only copy of failed
or unpersisted evidence. Packages without a typed lifecycle continue to use the legacy fixture lifecycle.

Package detachment and removal are explicit:

```text
acmm package detach <experiment-id>
acmm package uninstall <experiment-id>
acmm package uninstall <experiment-id> --detach-if-applied
acmm package purge
acmm package purge --detach-all
```

`detach` restores only subjects carrying that exact package revision to the LLM bindings declared by
the package. `uninstall` is strict, requires an explicit package selector, and refuses to remove a
revision still referenced by subject deployment provenance. `purge` is the intentionally global
store-cleanup command; `--detach-all` grants it permission to detach every attached revision first.

See `docs/experiments/package.md` for the package architecture and lifecycle.

## Subject environment management

Subject baselines are Core infrastructure rather than experiment definitions. The researcher-facing
environment surface is:

```text
acmm env set-llm <llm-id>
acmm env status
acmm env doctor --start-if-needed
acmm env stop
```

When `env doctor` is not ready, Core prints a final readiness summary that separates deployment context,
integration resources, Experiment Package runtime diagnostics, and subjects, then lists the exact blocking
checks. Package-owned diagnostics should report semantic blockers (for example, an uninitialized fixture
repository) rather than forcing Core to understand package-specific infrastructure.

`set-llm` reconciles qualified subjects to a registered LLM and returns them to experiment-independent capability state.
The internal `acmm-infra <subject> ...` commands remain detailed baseline/lifecycle escape hatches;
they do not select research experiments.

The current LLM comes from `infrastructure/llm-baselines/catalog.yaml`. Subject profiles remain
model-independent where possible and are composed with that registered LLM definition. See
`docs/infrastructure/llm_rebasing.md` and the subject-specific infrastructure READMEs.

## Execution selection and persistence

`acmm run` supports deterministic coordinate and semantic selection. Each `--run-index` accepts one
global planned-run index or inclusive range, and repeatable `--subject` filters select Contract
subject IDs. Those selectors are intersected; overlapping run-index selectors are deduplicated. For
subject-scoped positional work, `--subject-run-index` selects a zero-based index or range relative
to each selected subject while preserving the original global run identity. It requires `--subject`
and is mutually exclusive with `--run-index`. Relative indices follow the Contract's deterministic
subject subsequence after ordering/counterbalancing, so the same relative index need not name the
same scenario for different subjects.

When the desired coordinate is semantic rather than positional, repeatable `--scenario` and
`--repetition` filters select Contract scenario IDs and 1-based repetition numbers directly. These
filters are independent of counterbalanced position and may be combined with subject or index
filters. This is the preferred way to select the same execution category across counterbalanced
subjects.

```text
acmm run <experiment-id> --subject <subject-id> --run-index 20-39 --preview
acmm run <experiment-id> --subject <subject-id> --subject-run-index 0-9 --preview
acmm run <experiment-id> --scenario <scenario-id> --repetition 1 --preview
```

Every real execution verifies exact package, Contract, runtime, and declared integration provenance
before acquisition. Package runtime code is activated only from the verified immutable staged
snapshot and is composed with Core-owned adapters. Completed executions persist the Contract
snapshot, Run Records, evidence, execution summary, package provenance, and optional diagnostics.
Interrupted acquisition preserves already-written records plus `experiment_interruption.yaml` and
never writes a completed `experiment_execution.yaml`. Ctrl+C first checkpoints interruption intent,
quiesces any in-flight provider-gate admission, and then performs trusted cleanup while traffic
control remains active; additional Ctrl+C presses request a cooperative forced stop at the next safe
boundary instead of tearing through authority/resource cleanup.

`--capture-runtime-logs` records sanitized container diagnostics without adding them to scientific
evidence. Provider usage telemetry is likewise operational provenance only. When selected subjects use
Ollama Cloud, `acmm run` resolves `core:ollama_api_key` through the ACMM secret-binding registry and
records best-effort usage snapshots. Initialize the local secret store with `acmm secret init`;
secret source files are conventionally kept under `~/.config/acmm/secrets/`, while the versioned
registry remains `~/.config/acmm/secret-bindings.yaml`. If Ollama's immediate post-execution usage view is below the
known minimum request floor for the selected cloud-backed runs, ACMM briefly resamples with a bounded
settling schedule before persisting the final snapshot. Neither the key nor its local source path is
persisted or mounted into subject runtimes. Missing, lagging, or ambiguous telemetry never affects
experiment validity or ACMM scoring. See `docs/runtime/provider_usage.md` and
`docs/security/secrets.md`.

## Infrastructure and adapter boundaries

Core owns the qualified Hermes, Open WebUI, and Agent Zero baselines, their lifecycle managers, and
runtime adapters. Experiment Packages may provide generic integration components; Core loads those
components only from the installed staged package and never hard-codes the concrete external tool or
transport into its scientific model.

The internal baseline workflow remains available for diagnostics and integration work:

```text
acmm-infra <subject> init
acmm-infra <subject> apply
acmm-infra <subject> up
acmm-infra <subject> status
acmm-infra <subject> doctor
acmm-infra <subject> down
```

Research capability integrations are applied through `acmm package apply` or permissioned
`acmm run --apply-if-needed`; the internal subject CLI has no separate research-tool application
path.

See `infrastructure/README.md`, `docs/integration/agent_integration.md`, and the adapter documents under
`docs/runtime/`.

## Analysis workflow

Completed executions remain exportable and analyzable independently of package ownership:

```text
acmm export ~/acmm_generated/results/<experiment-id>/execution_<uuid>
python tools/analyze_execution.py ~/acmm_generated/results/<experiment-id>/execution_<uuid>
```

The general analysis pipeline owns normalized export, availability, requirement estimates,
uncertainty, and GOBPFR dimension aggregation. Experiment-specific analyses belong to Experiment
Packages or explicit package-owned analysis configuration. Core retains reusable statistical
capabilities, including generic interaction-analysis support, without registering historical studies
by experiment ID.

See `docs/analysis/`, `analysis/r/README.md`, and `tools/README.md`.
