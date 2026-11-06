# Secret Handling

ACMM repositories must not contain live API keys, tokens, passwords, or other credentials. Runtime
credentials are local operator configuration, not Experiment Package content and not reproducibility
artifacts.

## Local files

Store secrets outside both repository roots. For Ollama Cloud telemetry, the recommended location is:

```text
~/.config/acmm/ollama_api_key
```

The main repo and the experiment-package repo ignore common local secret paths such as `.env`,
`secrets/`, and `ollama_api_key`. `.gitignore` is only a first defense: it does not protect a secret
that is pasted into an already tracked source, documentation, YAML, or test file.

## Before committing or pushing

Always inspect staged changes and the final repository status before pushing:

```text
git diff --cached

git status --short
```

If the repositories are hosted on GitHub, enable Secret Protection / secret scanning and Push
Protection for both repositories when available. GitHub Push Protection can block a command-line
push when a supported secret is detected. This protection is stronger than `.gitignore` because it
can inspect secret-like content inside tracked files.

Push protection is a repository/account setting rather than a committed workflow file, so ACMM does
not attempt to emulate it with a CI workflow that would run only after a push has already reached the
remote.

## If a secret is committed

Treat a committed credential as exposed. Revoke or rotate it at the provider, remove it from the
working tree, and clean repository history when necessary. Merely adding the file to `.gitignore`
afterward does not remove an already committed secret.

## Experiment Package rule

Experiment Packages may describe which external capability is required, but they must never embed
live credentials needed to access it. Deployment/infrastructure code is responsible for obtaining
runtime credentials from operator-controlled configuration or environment variables.

## Experiment integration secret bindings

Experiment Package integrations declare generic secret slots; the package never stores the secret
value or a machine-specific source path. Bind a slot to a local file with `acmm secret bind`.
The default registry is `~/.config/acmm/secret-bindings.yaml` and stores only binding metadata and
source paths. Secret values are read only when an integration resolves its declared slot.

On POSIX systems, file-backed secret sources must not grant group/other permissions. A typical setup
is:

```text
chmod 600 ~/.config/acmm/github_mcp_access_token
```

Neither secret values nor secret digests belong in package provenance, deployment markers, status
output, or execution manifests.
