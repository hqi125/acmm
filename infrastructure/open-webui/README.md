# Open WebUI Infrastructure

Open WebUI is pinned by `infrastructure/open-webui/profile.yaml`; implementation lives under
`src/acmm_infra/subjects/open_webui/`.

The baseline owns provider wiring, isolation settings, persistent application configuration, and the
qualified model. Research capabilities are supplied by Experiment Package integrations rather than
Core-owned Tools.

Open WebUI has both environment and persistent-database configuration. The subject manager exposes
those as generic managed desired-state surfaces so an integration can request host configuration
without bypassing normal apply/status/doctor reconciliation.

Small executable container payloads live under
`src/acmm_infra/subjects/open_webui/payloads/` rather than inline in manager code.

Lifecycle:

```text
acmm-infra open-webui init
acmm-infra open-webui apply
acmm-infra open-webui up
acmm-infra open-webui status
acmm-infra open-webui doctor
acmm-infra open-webui down
```
