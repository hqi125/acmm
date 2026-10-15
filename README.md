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
- regression fixtures and tests;
- the `acmm validate` and `acmm plan` commands.

## Development environment

The current development layout keeps the Git repository on the Windows side while running
the ACMM Python environment and experiment infrastructure inside WSL.

A typical layout is:

```text
Windows
└── ACMM Git repository
    └── visible in WSL through /mnt/c/...

WSL
├── ~/acmm
│   └── experiment infrastructure
├── ~/acmm_repo
│   └── symbolic link to the Windows Git repository
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
