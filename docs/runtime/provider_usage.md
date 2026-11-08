# Provider Usage Observability

ACMM can record best-effort provider-side usage around an experiment execution. Provider usage is
operational provenance only. It does not affect Run Record validity, retries, ACMM judgments,
availability analysis, or statistical results.

## Ollama Cloud

When every selected LLM binding relevant to an execution uses the Ollama Cloud provider, `acmm run`
creates an Ollama Cloud usage monitor. The monitor samples the official `GET /api/usage` endpoint
immediately before experiment-level execution and initially again at execution completion with:

```text
range=24h
scope=self
group_by=model
```

The samples are persisted under:

```text
execution.provider_usage.ollama_cloud
```

The human-readable `execution_summary.txt` and the final `acmm run` console summary also report an
Ollama Cloud request-count delta. ACMM prefers complete model-scoped deltas for the selected models.
If the provider omits a required model group, ACMM falls back to the account-scope request delta and
labels that fallback explicitly. If telemetry is unavailable, the summary reports `N/A` with the
reason.

The provider's usage view can lag behind completed inference requests. ACMM therefore knows the
minimum expected request count from the selected Ollama Cloud-backed runs. If the immediate
post-execution snapshot is below that floor, ACMM performs bounded settling samples after 1, 2, 4,
and 8 seconds, stopping as soon as the model-scoped delta reaches the floor. The manifest records
the initial post-execution snapshot when settling was needed, the final snapshot, the wait schedule,
and whether the expected floor was reached.

A delta below the expected floor after settling is reported explicitly. It means provider telemetry
had not caught up within the bounded window (or was affected by another provider-side reporting
limitation); it does not change experiment validity. Likewise, a zero delta must not be interpreted
as proof that the agent made no inference requests.

### Cloud model names

Ollama uses `-cloud` aliases such as `gpt-oss:120b-cloud` when a local Ollama server proxies a cloud
model. Direct cloud usage reporting can identify the same model as `gpt-oss:120b`. ACMM treats the
non-`-cloud` name as an accepted provider-side alias while preserving the configured model name in
experiment provenance.

## API key

The usage endpoint requires direct Ollama Cloud authentication. ACMM Core resolves the credential
only through the global secret-binding registry using the Core-scoped key `core:ollama_api_key`.
There is no `acmm run` API-key option and no environment-variable fallback. The API key remains
optional for ACMM execution: without a valid binding, provider telemetry is recorded as unavailable
and the experiment proceeds normally.

Keep the key outside both ACMM repositories. A convenient WSL layout is:

```text
~/.config/acmm/secrets/ollama_api_key
```

Initialize the ACMM secret store once, then keep the key owner-only inside the managed secret
directory and bind it:

```text
acmm secret init
chmod 600 ~/.config/acmm/secrets/ollama_api_key
acmm secret bind core ollama_api_key --file ~/.config/acmm/secrets/ollama_api_key
```

Subsequent `acmm run` executions resolve the binding automatically. Neither the secret value nor its
local source path is persisted in execution artifacts or mounted into subject runtimes.

Do not place the key in `experiment.yaml`, `experiment-package.yaml`, integration files, `.env` files
inside either repository, command examples committed to Git, execution notes, or test fixtures.

## Interpretation limits

Ollama's usage endpoint is account-scoped rather than ACMM-execution-scoped. Concurrent requests on
the same account may therefore enter the observed delta. Provider usage groups can also be omitted
from the returned top groups, and provider snapshots may remain unchanged over a short execution.
For those reasons, usage telemetry is useful for debugging, qualification, and rough request-volume
checks, but is not an ACMM measurement dimension.

See `docs/security/secrets.md` for repository safeguards around API keys and other credentials.
