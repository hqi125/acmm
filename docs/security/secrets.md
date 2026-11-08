# Secret Handling

ACMM repositories must not contain live API keys, tokens, passwords, or other credentials. Runtime
credentials are local operator configuration, not Experiment Package content and not reproducibility
artifacts.

## Local secret store

Initialize the local store explicitly:

```text
acmm secret init
```

The default layout is:

```text
~/.config/acmm/
  secret-bindings.yaml
  secrets/
    ollama_api_key
    github_mcp_access_token
    acmm-experiment-2-mcp-subject-pat
    acmm-experiment-2-mcp-controller-pat
```

`acmm secret init` creates the `secrets/` directory with owner-only directory permissions on POSIX
systems and creates an empty current-version registry when no registry exists. It does not create,
copy, rotate, or delete credential files. Secret source files remain operator-owned and should use
mode `0600` on POSIX systems.

The main repo and the experiment-package repo ignore common local secret paths such as `.env`,
`secrets/`, and common API-key filenames. `.gitignore` is only a first defense: it does not protect a
secret pasted into an already tracked source, documentation, YAML, or test file.

## Before committing or pushing

Always inspect staged changes and final repository status before pushing:

```text
git diff --cached

git status --short
```

If the repositories are hosted on GitHub, enable Secret Protection / secret scanning and Push
Protection when available. Push Protection can block a command-line push when a supported secret is
detected. This protection is stronger than `.gitignore` because it can inspect secret-like content
inside tracked files.

Push protection is a repository/account setting rather than a committed workflow file, so ACMM does
not attempt to emulate it with a CI workflow that would run only after a push has already reached the
remote.

## If a secret is committed

Treat a committed credential as exposed. Revoke or rotate it at the provider, remove it from the
working tree, and clean repository history when necessary. Merely adding the file to `.gitignore`
afterward does not remove an already committed secret.

## Experiment Package rule

Experiment Packages may describe which external capability is required, but they must never embed
live credentials needed to access it. ACMM obtains runtime credentials only through the local secret
binding workflow; package code and Core services do not read secret values from ad hoc CLI options or
provider-specific environment variables.

## Secret binding scopes

The registry stores only binding metadata and local source paths, never secret values or secret
digests. Three scopes are available:

- **Core** bindings are consumed only by Core services. Example: `core:ollama_api_key`.
- **Experiment** bindings are declared by a package runtime and are available only to that package's
  trusted runtime context.
- **Integration** bindings are declared by a package integration and are available only to that
  integration.

Bind local files through one scope-explicit command family:

```text
acmm secret bind core SECRET_ID --file PATH
acmm secret bind experiment EXPERIMENT_ID SECRET_ID --file PATH
acmm secret bind integration EXPERIMENT_ID INTEGRATION_ID SECRET_ID --file PATH
acmm secret list
```

The full scoped key is the registry identity. A `secret_id` may therefore appear legitimately in
multiple scopes, experiments, or integrations. Rebinding an exact key to the same source file is
idempotent. Changing an existing exact key to a different source file requires `--replace`; Core
never silently retargets a binding.

Use the matching scope for removal:

```text
acmm secret unbind core SECRET_ID
acmm secret unbind experiment EXPERIMENT_ID SECRET_ID
acmm secret unbind integration EXPERIMENT_ID INTEGRATION_ID SECRET_ID
```

`acmm secret unbind` requires an exact scoped key. If that key is absent, the command fails and
reports same-name bindings under other scoped keys when present. Unbind removes only the registry
reference and never removes the secret source file. This prevents registry maintenance from
accidentally destroying a credential that may still be used elsewhere.

## Registry schema and upgrades

`~/.config/acmm/secret-bindings.yaml` is a versioned local data format. Every registry must declare a
non-empty `schema_version`. Core bundles one JSON Schema file per supported registry version and
validates the YAML document against the schema named by that field.

Ordinary operations (`bind`, `unbind`, `list`, and runtime secret resolution) require the registry
version used by the running Core. A structurally valid older registry is still rejected with an
explicit version-mismatch error. Core never upgrades or rewrites an older registry as a side effect
of another command.

Use:

```text
acmm secret validate
acmm secret upgrade
```

`acmm secret validate` requires the current Core version and validates both layers of the local
secret store. It first checks the registry schema and semantic key invariants, then checks every
bound source file without displaying its value. A bound source must still resolve to a regular file,
pass the owner-only permission policy on POSIX systems, be readable as UTF-8 text, and contain a
non-empty secret value. Validation reports all unusable bindings found in one pass so stale paths,
permission drift, and empty credentials can be repaired before provisioning begins.

`acmm secret upgrade` is the explicit version-to-version transformation command: it validates the
declared source schema, applies the registered migration step(s), validates the target schema, and
atomically replaces the registry. Current v0.1 registries upgrade to v0.2 by preserving every
existing Integration-scoped binding and adding the explicit `scope: integration` field required by
v0.2.

`acmm secret init` never performs this upgrade implicitly. If it finds a valid older registry, it
reports that an upgrade is required and leaves the registry content unchanged.

## Typical local setup

```text
acmm secret init
chmod 600 ~/.config/acmm/secrets/ollama_api_key
chmod 600 ~/.config/acmm/secrets/github_mcp_access_token
chmod 600 ~/.config/acmm/secrets/acmm-experiment-2-mcp-subject-pat
chmod 600 ~/.config/acmm/secrets/acmm-experiment-2-mcp-controller-pat
```

Neither secret values nor secret digests belong in package provenance, deployment markers, status
output, or execution manifests.
