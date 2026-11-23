# ACMM CLI Command Map

The `acmm` command is the researcher-facing interface to ACMM Core. The CLI coordinates existing subsystems and presents stable operator commands; domain rules remain owned by the runtime, package, record, Campaign, export, and analysis modules that implement them.

## Main command families

### Environment

```text
acmm env set-llm <llm-id>
acmm env status
acmm env doctor --start-if-needed
acmm env stop
```

Use `acmm env` for experiment-independent subject baseline management.

### Secrets

```text
acmm secret init
acmm secret bind ...
acmm secret validate
acmm secret list
acmm secret upgrade
```

Secrets remain local operator configuration. See [`../security/secrets.md`](../security/secrets.md).

### Experiment Packages

```text
acmm package validate <package-directory>
acmm package install <package-directory>
acmm package apply <experiment-selector>
acmm package list
acmm package show <experiment-selector>
acmm package detach <experiment-selector>
acmm package uninstall <experiment-selector>
```

Packages are immutable installed scientific revisions. See [`../experiments/package.md`](../experiments/package.md).

### Immediate execution

```text
acmm run <experiment-selector> --preview
acmm run <experiment-selector> [selection options] [execution options]
```

`acmm run` owns the immediate operator workflow across package resolution, deterministic selection, deployment preflight, readiness, execution, cleanup, persistence verification, and terminal presentation. The individual semantics remain implemented by their owning subsystems.

### Campaigns

```text
acmm campaign create <experiment-selector>
acmm campaign execute <campaign-id> ...
acmm campaign status <campaign-id>
acmm campaign resume <campaign-id> ...
acmm campaign adjudicate <campaign-id> --package <adjudicator-selector>
acmm campaign merge <campaign-id> ...
```

See [`campaign.md`](campaign.md).

### Export

```text
acmm export run <execution-directory>
acmm export campaign <campaign-directory> <merge-directory>
acmm export provider-pressure <provider-pressure-root>
```

Export is mechanical and deterministic. See [`../analysis/export.md`](../analysis/export.md).

### Analysis

```text
acmm analyze <export-directory> --plan <analysis-plan>
```

Analysis plans select generic R analysis/report/plot modules. See [`../analysis/plan.md`](../analysis/plan.md).

### Record validation

```text
acmm validate-record <run-record-file> ...
```

Run Record semantic rules are documented in [`../record/semantics.md`](../record/semantics.md).

## Lower-level infrastructure CLI

`acmm-infra` is a separate engineering/diagnostic command surface for individual subject deployments:

```text
acmm-infra <subject> init
acmm-infra <subject> apply
acmm-infra <subject> up
acmm-infra <subject> status
acmm-infra <subject> doctor
acmm-infra <subject> down
```

Normal research workflows should prefer `acmm env`, package lifecycle commands, and `acmm run`/`acmm campaign`.

## Ownership rule

The CLI owns parser assembly, command dispatch, terminal presentation, and the cross-subsystem ordering required by an operator workflow. It does not redefine Contract validation, run planning, persistence, statistical analysis, or package/integration semantics merely because those operations are reachable from a command.
