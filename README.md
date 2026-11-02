# ACMM

Agent Compliance Metric Model research and experiment infrastructure.

## Current implementation

The repository currently contains Experiment Contract v0.1, including:

- a Draft 2020-12 JSON Schema;
- a YAML authoring template;
- a semantic specification;
- a populated magic-number example;
- YAML loading and Contract validation;
- harness-compatibility validation;
- deterministic runtime planning;
- platform-neutral single-run execution orchestration;
- experiment-level plan execution with explicit invalid-attempt retries;
- a concrete Hermes Agent adapter using structured JSONL events;
- a real magic-number fixture/evaluator pilot and Hermes `acmm-tools` plugin;
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

Experiment Packages are the researcher-facing experiment unit. Validate package source without
installing or executing runtime code:

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

`execute_planned_run()` prepares the environment, invokes the subject adapter, captures
normalized evidence, evaluates scenario opportunities, computes evidence SHA-256 digests,
and constructs a Run Record.

Infrastructure exceptions are converted into Run Records with
`execution.validity: invalid` rather than being interpreted as compliance violations.

The returned `executed_run` remains in memory and contains both the Run Record and evidence
artifact bytes. Persistent result-directory writing is intentionally the next layer.

See `docs/runtime/execution.md` for the execution boundary and failure semantics.

## Result persistence

`persist_executed_run()` writes an in-memory execution result into:

```text
results/<experiment_id>/run_<run_index>_<run_id>/
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
results/<experiment_id>/execution_<execution_id>/
```

The session snapshots the exact Contract bytes before running any selected planned slots. By
default the complete deterministic plan is selected. Engineering/pilot executions may select
explicit run indices; the manifest records both the full Contract run count and the selected
indices so a subset cannot be mistaken for a complete Contract execution.

Every realized attempt is preserved. An attempt is retried only when
`execution.validity` is `invalid`; the first valid attempt resolves the planned slot.
Valid agent outcomes such as `timeout` or `max_steps` are not retried.

Contracts may additionally specify a fixed `run_protocol.pacing.inter_run_delay_seconds`.
The executor applies it between selected planned slots, not between retries and not after
the final slot, and records the configured pacing in the completion manifest. The
magic-number pilot currently uses a 2-second inter-run delay for conservative Cloud-backed
acquisition.

The session completion manifest, `experiment_execution.yaml`, is written only after the
selected plan has been traversed. If orchestration or persistence is interrupted, previously
published Run Records remain intact but the session has no completion manifest and cannot
be mistaken for a completed collection.

See `docs/runtime/experiment_execution.md` for the acquisition and retry semantics.

## Analysis-ready CSV export

A completed execution can now be projected into deterministic downstream analysis tables:

```text
acmm export results/<experiment_id>/execution_<execution_id>
```

The default output is separate from canonical results:

```text
exports/<experiment_id>/execution_<execution_id>/
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
    results/<experiment_id>/execution_<execution_id>
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

## First concrete pilot: Hermes magic number

The first real subject integration is Hermes Agent v0.21.3 with the existing
`nemotron-3-nano:30b-cloud` model baseline.

The runtime pieces are:

```text
src/acmm/adapters/hermes.py
    Hermes process/Docker bridge + session-export orchestration

src/acmm/adapters/hermes_session.py
    persisted Hermes-session parsing + canonical evidence normalization

src/acmm/pilots/magic_number.py
    magic-number fixture + deterministic opportunity evaluators

infrastructure/hermes/experiments/magic_number_tool_use_v1/plugins/acmm-tools/
    pilot-owned Hermes plugin exposing get_magic_number and add_numbers
```

The adapter runs a quiet Hermes one-shot chat and then exports that exact persisted
session as JSONL rather than scraping the interactive terminal UI. The normal live path
relies on the already-qualified model/provider route and verifies the model reported by the
exported session against the Contract. Runtime environment injection is generic: the
adapter consumes a string environment-variable mapping, while the concrete pilot fixture
owns translations such as `magic_number=87 -> ACMM_MAGIC_NUMBER=87`.

The magic number is provided by the runtime fixture and injected into the Hermes process or
Docker container as `ACMM_MAGIC_NUMBER`. It is not hard-coded into the Hermes plugin.

The evaluators judge actual tool calls and tool results. In particular, claiming that the
magic number was 87 does not satisfy the calculation opportunity if the actual
`add_numbers` call used a different value.

See:

```text
docs/runtime/hermes_adapter.md
docs/infrastructure/hermes.md
infrastructure/hermes/README.md
```

Because this checkpoint adds new Python subpackages (`acmm.adapters` and `acmm.pilots`),
rerun the editable installation once after merging it:

```text
python -m pip install -e ".[test]"
pytest
```

Before the live execution command, the repository now includes a reproducible infrastructure
management layer.

A new agent should pass a broader qualification workflow before it is treated as a comparative
ACMM subject: managed desired state, machine-interface study, runtime adapter/evidence
normalization, exact termination mapping, live smoke, and subject-only stress qualification. See
`docs/integration/agent_integration.md` for the general integration workflow and coding surface.
The pinned Agent Zero v2.14 compatibility profile and its 60-run stress qualification are recorded
in `docs/integration/agent_zero_qualification.md`.

## Second controlled experiment: scoped record update

`controlled_scoped_record_update_v1` adds the first direct empirical coverage of the canonical
`goal` and `boundary` dimensions. Its controlled workspace exposes `source`, `target`, and
`protected` records through narrow read/write Tools plus two functional but prohibited bypass
capabilities. Four scenarios cover normal success, an out-of-scope distractor, source-read
failure pressure, and target-write failure pressure.

The experiment owns independent apparatus under each subject's
`infrastructure/<subject>/experiments/controlled_scoped_record_update_v1/` tree. Its fixture uses
the generic per-run Tool-configuration envelope consumed by Open WebUI and Agent Zero, while
Hermes continues to receive the same fixture state as ordinary environment variables. The
adapters therefore remain experiment-independent.

The canonical Contract lives at:

```text
experiments/examples/controlled_scoped_record_update/experiment.yaml
```

It plans 60 runs and 150 atomic opportunities: 30 Goal opportunities in the two deliberately
feasible scenarios and 120 Boundary opportunities across all four scenarios.

## Open WebUI infrastructure checkpoint

Open WebUI now has a repository-managed deployment and Tool baseline alongside Hermes. The
pinned subject is Open WebUI `v0.11.4` at the qualified image digest recorded in
`infrastructure/open-webui/profile.yaml`. The lifecycle is:

```text
acmm-infra open-webui init
acmm-infra open-webui apply --experiment controlled_tool_use_multiscenario_v1
acmm-infra open-webui up
acmm-infra open-webui status
acmm-infra open-webui doctor
acmm-infra open-webui down
```

`apply` without `--experiment` is the experiment-independent Open WebUI baseline reconciler:
it updates deployment state, temporarily starts the backend, waits for readiness, reconciles
persistent provider settings, removes active experiment Tools, and then stops the provisioning
service. `apply --experiment <experiment-id>` composes the baseline with the selected experiment
apparatus and reconciles that effective state in one provisioning pass. `apply-tools --experiment <experiment-id>` is the narrower
Tool-only path. `up` waits for `/ready` before returning.
`status` and `doctor` discover the active experiment context from the deployment marker;
`doctor` checks the baseline and exact selected Tool state together. ACMM does not modify `webui.db` directly. See
`infrastructure/open-webui/README.md` for the current boundary.

The experiment-facing Open WebUI runtime adapter now lives under `src/acmm/adapters/`. It
uses the backend native multi-round Tool loop, creates and removes fresh chat state per run,
sets controlled Tool Valves from the fixture, and normalizes persisted Tool activity into the
existing ACMM evidence channels. See `docs/runtime/open_webui_adapter.md`. The frozen Experiment 1 binding remains unchanged.

## Agent Zero infrastructure checkpoint

Agent Zero now has the same repository-managed lifecycle surface as Hermes and Open WebUI.
The pinned subject is Agent Zero `v2.14` at the immutable multi-platform image digest recorded
in `infrastructure/agent-zero/profile.yaml`. The managed lifecycle is:

```text
acmm-infra agent-zero init
acmm-infra agent-zero apply
acmm-infra agent-zero apply --experiment controlled_tool_use_multiscenario_v1
acmm-infra agent-zero up
acmm-infra agent-zero status
acmm-infra agent-zero doctor
acmm-infra agent-zero down
```

Agent Zero persists only its `/a0/usr` user-state tree into the ACMM runtime root; framework
code remains owned by the pinned image. Baseline `apply` reconciles deployment state, the `acmm`
Agent Profile, the v2.14 `_model_config` `Default` preset, and experiment-isolation files,
including narrow local-model communication/solving/initial-message prompt overrides. It also
removes active experiment plugins and returns the Tool allow-list to empty.
`apply --experiment <experiment-id>` composes that baseline with the selected experiment
plugin and Tool allow-list, then reconciles the effective state in one provisioning pass. `apply-tools --experiment <experiment-id>` applies only
the experiment-facing Tool/plugin layer.

The qualified Agent Zero baseline pins main and utility roles to
`nemotron-3-nano:30b-cloud` through Windows Ollama with 64K contexts. The `acmm` profile
blocks all experiment Tools, Skills, and MCP Tools by default; a selected experiment apparatus
adds only its own Tool allow-list. The baseline also disables memory
recall/memorization/consolidation and automatic chat naming, and disables MCP/A2A servers,
update checks, project inheritance, and automatic RFC Docker behavior. `doctor` verifies
persistent settings, effective model config, and model availability in addition to the
deployment layer. See `infrastructure/agent-zero/README.md`.

The experiment-facing Agent Zero runtime adapter now lives under `src/acmm/adapters/`. It
reuses the persistent running Agent Zero service, configures per-run `acmm_tools` state through
the supported plugin API, creates and removes a fresh context, executes through the external
API, and normalizes Agent Zero Tool logs into the existing ACMM evidence channels. See
`docs/runtime/agent_zero_adapter.md`; the standalone live smoke helper is
`tools/smoke_agent_zero.py`.

## Infrastructure management

`acmm` now separates experiment-package lifecycle from subject-environment lifecycle. A normal
experiment workflow is:

```text
acmm package validate ../acmm-experiments/minimal_agent_baseline_v1
acmm package install ../acmm-experiments/minimal_agent_baseline_v1
acmm package apply minimal_agent_baseline_v1
acmm run minimal_agent_baseline_v1 --preview
acmm run minimal_agent_baseline_v1

# Or let run reconcile selected experiment context when needed:
acmm run minimal_agent_baseline_v1 --apply-if-needed
```

Experiment Package source directories are validated and copied into the ACMM-managed package store
before their runtime code or apparatus is consumed. `package apply` accepts only an already-installed
package selector; it never acquires package source implicitly. Multiple revisions of the same
experiment may be installed simultaneously. A plain experiment ID selects it only when one revision
is installed; otherwise use `EXPERIMENT_ID@SHA_PREFIX`, as printed by `package install` and
`package list`. Package sources are local filesystem directories; ACMM does not install them with
`pip` or automatically download executable experiment code.

Package management also provides:

```text
acmm package list
acmm package show minimal_agent_baseline_v1
acmm package uninstall minimal_agent_baseline_v1
```

`package uninstall` refuses to remove a revision that is still referenced by deployed subject
state. Reconcile those subjects to the intended LLM environment first.

`acmm run` consumes only already-installed Experiment Packages. It does not accept a raw Contract
path, acquire a package source implicitly, or infer an experiment from deployment state. The
experiment/package selector is mandatory.

`acmm env` no longer selects or applies experiments. Its only state-setting operation is explicit
LLM selection:

```text
acmm env set-llm <llm-id>
acmm env status
acmm env doctor --start-if-needed
acmm env stop
```

`set-llm` reconciles the selected qualified subjects to that registered LLM and removes any active
experiment apparatus from them. `status` and `doctor` may still report observed package/deployment
provenance because that is useful diagnostic evidence, but they do not choose an experiment. `stop`
only stops running qualified subject runtimes and does not mutate apparatus.

`acmm-infra` remains strictly subject-facing: `acmm-infra hermes ...`, `open-webui ...`, and
`agent-zero ...` are the detailed lifecycle and diagnostic escape hatches.

For Hermes:

```text
~/acmm_repo/
└── infrastructure/hermes/
    ├── profile.yaml
    ├── defaults/
    └── experiments/<experiment-id>/
        ├── apparatus.yaml
        └── plugins/...

~/acmm_runtime/
└── hermes/
    ├── compose.yaml
    ├── .env
    └── data/
```

The preferred fresh workflow is now fully non-interactive on the Hermes configuration side:

```text
acmm-infra hermes init
acmm-infra hermes apply
acmm-infra hermes up
acmm-infra hermes doctor
```

`apply` reproduces the qualified Windows-Ollama route:

```text
provider       custom
base URL       http://host.docker.internal:11434/v1
API mode       chat_completions
model          nemotron-3-nano:30b-cloud
context        64000
reasoning      medium
terminal       local
Blank Slate    enabled
```

The Blank Slate baseline keeps only `file`, `skills`, `terminal`, and `vision` in the
default CLI toolset surface; disables compression, memory/user profile, checkpoints, and
smart model routing; uses a 90-turn baseline; and opts out of the full bundled-skill
catalog while retaining Hermes's essential skill. The `acmm` plugin toolset is an explicit
experiment overlay rather than a default toolset.

ACMM then applies a separate experiment-isolation policy on top of official Blank Slate:
automatic title generation and background review are disabled, and Hermes Tool Search is
set to `off`. These are ACMM methodological controls intended to remove auxiliary model
calls and tool-routing indirection; they are not part of Hermes's definition of Blank
Slate.

Experiment apparatus is reconciled separately from the qualified baseline:

```text
acmm-infra hermes down
acmm-infra hermes apply --experiment controlled_tool_use_multiscenario_v1
acmm-infra hermes up
acmm-infra hermes doctor
```

`apply --experiment <experiment-id>` composes the stable Hermes baseline with the selected
experiment plugin/tool-routing policy and reconciles that effective state in one provisioning
batch. `apply-tools --experiment
<experiment-id>` performs only the experiment-facing plugin/tool reconciliation. Baseline
`apply` returns Hermes to the experiment-independent state.

`apply` requires the persistent Hermes gateway to be stopped. It reconciles all Hermes
settings and managed-plugin enablement inside one bounded one-off Compose container rather
than starting a new container for every setting. The batch prints sub-operation progress
live and has a configurable whole-batch timeout (`--timeout-seconds`, default 120). On
timeout, failure, or interruption, the named provisioning container is cleaned up.

Windows Ollama retains responsibility for Cloud authentication, so fresh Hermes setup does
not require copying an Ollama Cloud key into the Hermes data directory.

Experiment toolset selection remains outside `init`; the experiment adapter chooses it per
run.

Existing Hermes data adoption remains available as a migration fallback:

```text
acmm-infra hermes adopt-data /path/to/old/data --yes
```

See `docs/infrastructure/hermes.md`.

After `doctor` reports `READY`, execute only the first deterministic magic-number slot as
the live smoke test:

```text
acmm run magic_number_tool_use_v1 --run-index 0
```

`acmm run` also supports deterministic selection without shell loops. Each `--run-index`
accepts either one index or an inclusive range, and repeatable `--subject` filters select
Contract subject/agent IDs. Different selector types are intersected and overlapping index
selectors are deduplicated. For example:

```text
acmm run controlled_tool_use_multiscenario_v1 \
    --subject open_webui_nemotron30b --run-index 20-39
```

Use `--preview` with the same selectors to validate and print the resolved run-index, subject,
scenario, and repetition breakdown without creating an execution directory or contacting the
subject runtimes. For an Experiment Package, preview still activates trusted package runtime code
from the staged snapshot so harness compatibility and runtime declarations can be checked; use
`acmm package validate` when package code must not be executed. Preview also inspects the selected
subjects' recorded deployment contexts; context mismatches are warnings in preview mode so planning
remains available before deployment. When context is divergent, preview also reports whether strict
mode would fail or whether `--apply-if-needed` grants permission to reconcile the selected subjects.
Preview never performs that reconciliation.

Before a real acquisition, `acmm run` performs package-deployment and live-readiness checks for
exactly the selected subjects. The selected deployment must record the exact installed package,
Contract, runtime, and per-subject apparatus identities; a package may legitimately declare no
apparatus for a subject. Plain `acmm run` remains strict: deployment divergence aborts before an
execution session is created. `--apply-if-needed` grants permission to run the same package-apply
workflow for only the subjects required by the selected run set. ACMM then repeats the strict
deployment preflight from persisted state; execution remains blocked unless that second preflight
passes.

After deployment provenance is ready, every selected subject must report `READY` through the same
passive doctor logic used by `acmm-infra <subject> doctor`.

`acmm run --start-if-needed` grants a narrower lifecycle permission. Correctly deployed subjects
that are stopped are marked deferred during initial live preflight and are started lazily when their
first selected run is reached. ACMM runs the doctor check immediately after startup, keeps only the
currently needed ACMM-owned runtime alive when selected runs move between subjects, and stops only
runtimes that ACMM itself started. A subject that was already running is never stopped by this
policy. Wrong package/apparatus state and an already-running but unhealthy subject remain hard
errors; the option is not an automatic repair mechanism. `--startup-timeout-seconds` controls the
startup allowance. Repository and runtime roots are infrastructure configuration rather than run
selection options; `ACMM_RUNTIME_ROOT` selects a non-default runtime/package store when needed.
`--preview` performs only the local deployment-context inspection and never contacts or starts
subject runtimes, even when `--start-if-needed` is present.

`--capture-runtime-logs` enables best-effort diagnostic capture for real executions. Each subject
runtime window is bounded from ACMM's acquisition point through the point immediately before that
subject is released or the execution exits. Docker output is sanitized at the artifact boundary: ANSI
terminal presentation sequences and non-text control effects are removed, carriage-return/progress
output is normalized to readable lines, and the resulting plain UTF-8 text is written as
`diagnostics/<subject_id>/container.log`; a later lease uses `container_2.log`, and so on. Each log
has a neighboring `.meta.json` sidecar recording the sanitizer version, input/output UTF-8 byte
counts, and transformation counts. Sanitization does not modify the running container or the data
used by adapters/evaluators. These files are operational diagnostics only: they are not Run Record
evidence, do not participate in compliance adjudication, and do not affect Contract or apparatus
hashes. A log-capture or metadata-write failure produces a warning but does not invalidate the
experiment. Preview mode never creates diagnostic files.

`acmm run` requires one explicit installed-package selector:

```text
acmm run EXPERIMENT_ID --preview
acmm run EXPERIMENT_ID@SHA_PREFIX --preview
```

A plain experiment ID is accepted only when exactly one installed revision exists. When multiple
revisions are installed, the digest-qualified selector is mandatory. Deployment markers are used
only to verify that the selected package is actually applied to the selected subjects; they never
choose the experiment on behalf of the operator.


The command opens and verifies the installed Experiment Package, checks harness compatibility,
and resolves each Contract subject through the qualified subject execution catalog. Package runtime
code is activated only from the verified staged snapshot and composed with the same core-owned
subject adapters. Execution
persists the exact Contract snapshot, Run Record, evidence artifacts, and package provenance when
applicable, then re-validates the persisted Run Record. Agent-specific runtime wiring stays in the
subject catalog rather than in experiment packages or the top-level experiment CLI.
The subject catalog also declares which per-run termination controls each qualified runtime can
enforce. Execution fails before acquisition when a Contract requests an unsupported control.
Hermes supports the current `max_steps` plus `timeout_seconds` policy; smoke-qualified Open WebUI
and Agent Zero bindings currently support timeout-only Contracts because neither product exposes
an exact supported per-run mapping for `max_steps`.
The Contract's Hermes subject configuration also records the required ACMM experiment-isolation
controls (title generation off, background review off, and Tool Search off), so the persisted
Contract hash binds each run to that methodological baseline. Standalone `doctor` remains useful for
operator diagnostics; real `acmm run` now reuses that readiness logic automatically as its final
pre-execution gate.

At the end of `acmm run`, the terminal now keeps only a compact aggregate completion report with
paths, selected/resolved counts, termination totals, UTC start/end timestamps, and total duration.
The former verbose per-run replay is persisted as `execution_summary.txt` inside the execution
directory so long acquisitions do not flood the console while the full human-readable detail is
still retained.

Ctrl+C during acquisition is handled as an interrupted execution rather than a completed one.
Already persisted Run Records remain intact, `experiment_interruption.yaml` records the resolved,
unfinished, and not-started selections, and `execution_summary.txt` reports the evidence that was
actually persisted. `experiment_execution.yaml` is deliberately not written, so an interrupted
session cannot be exported as a complete experiment. Continuation/resume is intentionally not
supported; start a new execution instead.

Omit both `--run-index` and `--subject` only when the complete Contract plan is desired.
Deterministic CSV export remains the next layer.

