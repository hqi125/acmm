# Agent Zero Runtime Adapter

The Agent Zero adapter executes one run through Agent Zero's external API using the qualified `acmm`
Agent Profile. It creates a fresh context, sends the scenario prompt, reads the resulting log/evidence,
and terminates the context during cleanup.

Research capabilities are configured by Experiment Package integrations through Agent Zero's native
host mechanisms and policy. The runtime adapter does not write plugin fixture configuration or
restore synthetic tool state.

Observed Agent Zero tool records are normalized into ACMM tool-call/tool-result evidence without
stripping their native identity. This keeps unexpected or host-specific tool behavior visible for
analysis.
