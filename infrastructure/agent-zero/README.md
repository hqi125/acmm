# Agent Zero Infrastructure

Agent Zero is pinned by `infrastructure/agent-zero/profile.yaml`; implementation lives under
`src/acmm_infra/subjects/agent_zero/`.

The baseline owns provider/model configuration, the `acmm` Agent Profile, isolation policy, native
tool/MCP access policy, and persistent application settings. No research capability is enabled by the
baseline. Experiment Package integrations may compose desired host settings and policy using the
subject's generic managed-configuration surfaces.

Generated Agent Zero profile/configuration documents live as source templates under:

```text
src/acmm_infra/subjects/agent_zero/payloads/
```

The manager fills only variable fields before installation; JSON/YAML payload documents are not
embedded inside Python manager source.

Lifecycle:

```text
acmm-infra agent-zero init
acmm-infra agent-zero apply
acmm-infra agent-zero up
acmm-infra agent-zero status
acmm-infra agent-zero doctor
acmm-infra agent-zero down
```
