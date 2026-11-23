# Secret Handling

ACMM repositories must not contain live API keys, personal access tokens, passwords, private keys, or other credentials. Runtime credentials are local operator configuration; they are not Experiment Package content and are not reproducibility artifacts.

## Local secret store

Initialize the local store explicitly:

```text
acmm secret init
```

Core maintains a versioned local binding registry and a local secrets directory outside the repositories. The default location is under the operator's ACMM configuration directory, but commands should refer to credential files through explicit local paths rather than assuming a repository-relative location.

A secret source file must remain operator-controlled. On POSIX systems, use owner-only permissions where practical:

```text
chmod 600 <secret-file>
```

`<secret-file>` means a local file containing exactly one credential value. It must not be placed inside the Core or Experiment Package repository.

## Binding scopes

The registry stores binding metadata and local source paths, never secret values or secret digests.

Three scopes are available:

- **Core** — consumed only by Core services, for example provider-usage telemetry.
- **Experiment** — available only to the trusted runtime of one Experiment Package.
- **Integration** — available only to one declared package integration.

Bind a credential through the matching scope:

```text
acmm secret bind core <secret-id> --file <secret-file>
acmm secret bind experiment <experiment-id> <secret-id> --file <secret-file>
acmm secret bind integration <experiment-id> <integration-id> <secret-id> --file <secret-file>
acmm secret list
```

The full scoped key is the registry identity. Rebinding the same key to the same source file is idempotent. Retargeting an existing key requires the explicit replacement option supported by the CLI.

Remove only the registry reference with the corresponding scope:

```text
acmm secret unbind core <secret-id>
acmm secret unbind experiment <experiment-id> <secret-id>
acmm secret unbind integration <experiment-id> <integration-id> <secret-id>
```

Unbinding never deletes the source file.

## Validation and registry upgrades

Validate the registry and all bound source files without displaying secret values:

```text
acmm secret validate
```

When Core reports that an older registry version must be upgraded, perform the migration explicitly:

```text
acmm secret upgrade
```

`acmm secret init` does not silently upgrade an existing registry. Ordinary runtime commands also refuse to reinterpret an unsupported registry version.

## Experiment Package rule

A package may declare which credentials its runtime or integrations require, but it must never embed the values. Core resolves declared secrets only through the local binding registry. Secret values must not be passed through committed command examples, package manifests, integration configuration, test fixtures, or generated scientific provenance.

## Before committing or publishing

At minimum, inspect staged content and repository status:

```text
git diff --cached
git status --short
```

For a public repository, also scan the complete Git history. A credential that appeared in an earlier commit remains exposed even if the current working tree is clean.

When the repository is hosted on a platform that provides secret scanning or push protection, enable those controls as an additional defense. They complement local review; they do not replace it.

## If a secret is committed

Treat the credential as exposed:

1. revoke or rotate it at the provider;
2. remove it from the working tree;
3. clean repository history when required; and
4. verify the rewritten history before publication.

Adding a path to `.gitignore` after a credential was committed does not remove the credential from history.

## Typical local setup

A minimal Core-scoped binding looks like:

```text
acmm secret init
chmod 600 <secret-file>
acmm secret bind core ollama_api_key --file <secret-file>
acmm secret validate
```

Package-specific bindings use the experiment or integration scope declared by that package. Neither secret values nor local secret-file paths belong in scientific Run Records, execution manifests, package provenance, or public documentation examples.
