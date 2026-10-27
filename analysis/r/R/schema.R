acmmExportSchemaVersion <- "0.1"

acmmRunColumns <- c(
    "export_schema_version",
    "execution_id",
    "experiment_id",
    "contract_sha256",
    "harness_version",
    "execution_contract_run_count",
    "execution_selected_run_count",
    "execution_resolved_run_count",
    "max_attempts_per_run",
    "inter_run_delay_seconds",
    "run_index",
    "attempt",
    "selected",
    "run_id",
    "subject_id",
    "scenario_id",
    "repetition",
    "started_at",
    "ended_at",
    "termination",
    "validity",
    "failure_kind",
    "failure_message",
    "agent_name",
    "agent_version",
    "model_name",
    "model_provider",
    "subject_configuration_json",
    "environment_fixture",
    "environment_configuration_json",
    "evidence_count",
    "judgment_count",
    "result_path"
)

acmmJudgmentColumns <- c(
    "export_schema_version",
    "execution_id",
    "experiment_id",
    "contract_sha256",
    "run_index",
    "attempt",
    "selected",
    "run_id",
    "subject_id",
    "scenario_id",
    "repetition",
    "validity",
    "termination",
    "opportunity_id",
    "outcome",
    "requirement_id",
    "dimension",
    "check_id",
    "evaluator",
    "evidence_ids_json",
    "detail"
)

acmmTerminationValues <- c(
    "agent_completed",
    "max_steps",
    "timeout",
    "harness_error"
)

acmmValidityValues <- c("valid", "invalid")

acmmPopulationPolicies <- c("strict", "available-valid")

acmmOutcomeValues <- c(
    "compliant",
    "violation",
    "not_applicable",
    "unadjudicable"
)

acmmDefaultGroupBy <- c(
    "subject_id",
    "requirement_id",
    "dimension"
)

acmmDefaultDimensionGroupBy <- c(
    "subject_id",
    "dimension"
)

acmmDimensionMethods <- c(
    "arithmetic",
    "geometric",
    "harmonic",
    "minimum"
)
