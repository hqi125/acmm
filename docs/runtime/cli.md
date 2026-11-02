# ACMM CLI Responsibility Boundary

## Purpose

`src/acmm/cli.py` is the top-level researcher-facing command surface. It should coordinate
existing ACMM subsystems rather than become the owner of experiment, runtime, record, analysis,
or infrastructure business logic.

The CLI owns:

- top-level parser assembly and command dispatch;
- researcher-facing terminal presentation;
- cross-subsystem orchestration for `acmm run`;
- conversion of subsystem failures into stable command exit behavior.

The CLI does not own domain logic merely because that logic is reached from a command.

## Ownership scan checkpoint

The EP-5H responsibility scan moved code only where an existing subsystem already had a clear
claim. No new subsystem or folder was invented merely to shrink `cli.py`.

### Runtime planning

Run-index parsing, run/subject filtering, and selected-subject ordering are owned by
`acmm.runtime.planner`:

```text
Experiment Contract
    -> experiment_plan
    -> select_planned_runs()
    -> selected_subject_ids()
```

The CLI supplies selectors and renders the resulting plan. It does not define selection
semantics.

### Schema locations

The default Contract and Run Record schema paths are owned by their validators:

```text
acmm.contract.validator.default_contract_schema_path()
acmm.record.validator.default_run_record_schema_path()
```

CLI, package management, and analysis/export reuse those definitions instead of reconstructing
repository paths independently.

### Execution summary artifacts

Human-readable `execution_summary.txt` persistence and execution timestamp/duration formatting
are owned by `acmm.runtime.execution_summary`.

The terminal-facing completion/interruption display remains in the CLI because it is presentation
rather than canonical result persistence.

## Responsibilities intentionally retained in `cli.py`

The `run` command remains a substantial orchestration function because it crosses several
independent subsystem boundaries in a specific operator workflow:

```text
installed Experiment Package resolution
    -> package runtime activation
    -> harness compatibility
    -> run selection
    -> subject binding resolution
    -> deployment preflight / optional apply-if-needed
    -> live readiness / optional start-if-needed
    -> runtime log-window orchestration
    -> registry composition
    -> experiment execution
    -> cleanup
    -> persisted-record verification
    -> terminal presentation
```

Splitting those steps into arbitrary modules solely to reduce line count would hide the workflow
rather than improve ownership. Individual steps should move only when an existing subsystem gains
a reusable domain operation that naturally owns them.

The same rule applies to the small `export` and `validate-record` command wrappers: the analysis
and record subsystems own the work; the CLI owns argument translation, status reporting, and exit
codes.
