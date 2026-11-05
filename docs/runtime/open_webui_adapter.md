# Open WebUI Runtime Adapter

The Open WebUI adapter creates a fresh saved chat for each run, executes the scenario through the
pinned Open WebUI backend, captures persisted structured function-call/function-result activity and
the final response, then deletes the run chat during cleanup.

Experiment-specific external capabilities are configured before execution by the selected Experiment
Package integration through Open WebUI's native host configuration. The adapter does not update Tool
Valves or install Core-owned research Tools.

Normalized evidence preserves the observed native tool name, arguments, outputs, error status, and
call linkage whenever the host provides them.
