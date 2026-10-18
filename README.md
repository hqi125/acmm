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
- the `acmm validate` and `acmm plan` commands;
- the separate `acmm-infra` deployment/inspection command.

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

### 9. Validate an experiment

Validate the populated example Contract:

```text
acmm validate experiments/examples/magic_number/experiment.yaml
```

Inspect the deterministic run plan without executing the experiment:

```text
acmm plan experiments/examples/magic_number/experiment.yaml
```

Validate an intentionally invalid fixture:

```text
acmm validate tests/fixtures/contracts/invalid/unresolved_requirement.yaml
```

The valid Contract should return exit status `0`. Invalid Contracts return a nonzero exit
status, which can later be used by scripts and CI.

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
acmm validate experiments/examples/magic_number/experiment.yaml
acmm plan experiments/examples/magic_number/experiment.yaml
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

The researcher-facing command:

```text
acmm plan <experiment.yaml>
```

validates the Contract and, if valid, prints the deterministic run population without
executing any agent. Validation errors block planning; warnings are reported but do not.

After validation, `build_experiment_plan()` expands a Contract into the deterministic run
population defined by:

```text
subjects x scenarios x repetitions
```

Plan ordering follows subject declaration order, then scenario declaration order, then
one-based repetition order. Each planned run receives a zero-based `run_index` for its
position in the plan.

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

`execute_experiment()` walks the complete deterministic plan and stores one execution
session under:

```text
results/<experiment_id>/execution_<execution_id>/
```

The session snapshots the exact Contract bytes before running any planned slots.

Every realized attempt is preserved. An attempt is retried only when
`execution.validity` is `invalid`; the first valid attempt resolves the planned slot.
Valid agent outcomes such as `timeout` or `max_steps` are not retried.

The session completion manifest, `experiment_execution.yaml`, is written only after the
whole plan has been traversed. If orchestration or persistence is interrupted, previously
published Run Records remain intact but the session has no completion manifest and cannot
be mistaken for a completed collection.

See `docs/runtime/experiment_execution.md` for the acquisition and retry semantics.

## Statistical-analysis boundary

The experiment automation intentionally stops before statistical interpretation.

Canonical Contracts, Run Records, execution manifests, and evidence remain the source of
truth. A later deterministic export layer will project selected experimental facts into
CSV tables for a separate R/RStudio analysis workspace.

ACMM statistics such as compliance proportions, `S_D`, confidence intervals, statistical
models, and plots do not belong in `src/acmm`.

See `docs/analysis/boundary.md` for the full data-flow rule.

## First concrete pilot: Hermes magic number

The first real subject integration is Hermes Agent v0.21.3 with the existing
`nemotron-3-nano:30b-cloud` model baseline.

The runtime pieces are:

```text
src/acmm/adapters/hermes.py
    Hermes process/Docker bridge + structured event normalization

src/acmm/pilots/magic_number.py
    magic-number fixture + deterministic opportunity evaluators

infrastructure/hermes/plugins/acmm-tools/
    repository-owned Hermes plugin exposing get_magic_number and add_numbers
```

The adapter uses Hermes one-shot `stream-json` output rather than scraping the interactive
terminal UI. The Contract model is passed explicitly; provider routing is left to the
already-qualified Hermes profile unless an explicit adapter override is supplied.

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

## Infrastructure management

`acmm-infra` separates the repository's installation definitions from mutable deployed
agent state.

For Hermes:

```text
~/acmm_repo/
└── infrastructure/hermes/
    ├── profile.yaml
    ├── defaults/
    └── plugins/acmm-tools/

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

After `doctor` reports `READY`, the next runtime step is the researcher-facing live
magic-number execution command, followed by inspection of the first persisted session and
then deterministic CSV export.

