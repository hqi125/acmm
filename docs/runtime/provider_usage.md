# Provider Usage Observability

ACMM can record best-effort provider-side usage around an experiment execution. Provider usage is operational provenance only. It does not affect Run Record validity, retries, compliance judgments, availability analysis, or statistical results.

## Ollama Cloud

When the relevant selected LLM bindings use Ollama Cloud, `acmm run` can sample the provider usage endpoint before and after experiment-level execution. The persisted execution manifest records the observations and the human-readable execution summary reports a best-effort request-count delta.

Provider usage views can lag completed inference requests. ACMM therefore uses a bounded post-execution settling schedule when the immediate observation is below the known minimum request floor for selected cloud-backed runs. A lagging or unavailable usage view never changes scientific validity.

Provider-side aliases may differ from the configured local model name. ACMM preserves configured model provenance while accepting the known provider-side alias where required for usage matching.

## API key

Direct usage telemetry requires an Ollama Cloud credential. Core resolves it only through the local secret-binding registry using the Core-scoped key `core:ollama_api_key`.

There is no `acmm run` API-key command option and no repository-local environment-variable fallback. Execution remains valid when telemetry authentication is unavailable; usage is simply recorded as unavailable.

A typical binding is:

```text
acmm secret init
chmod 600 <secret-file>
acmm secret bind core ollama_api_key --file <secret-file>
acmm secret validate
```

`<secret-file>` is an operator-controlled local credential file outside both repositories. Neither the value nor the local path is persisted into scientific artifacts or mounted into subject runtimes.

See [`../security/secrets.md`](../security/secrets.md).

## Interpretation limits

Provider usage is usually account-scoped rather than ACMM-execution-scoped. Concurrent provider activity, delayed aggregation, omitted model groups, and provider reporting behavior can therefore affect the observed delta.

Use this telemetry for debugging, qualification, and rough request-volume checks. Do not treat it as an ACMM measurement dimension or as proof of exact per-run model-call counts.
