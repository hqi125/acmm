acmmExportSchemaVersion <- "0.1"
acmmCampaignExportSchemaVersion <- "0.2"
acmmExportSchemaVersions <- c(acmmExportSchemaVersion, acmmCampaignExportSchemaVersion)

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
    "provider_error",
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

acmmCampaignPlannedRunColumns <- c(
    "export_schema_version",
    "campaign_id",
    "experiment_id",
    "design_digest",
    "contract_sha256",
    "package_sha256",
    "run_index",
    "logical_run_id",
    "subject_id",
    "scenario_id",
    "repetition",
    "materialization_key",
    "observed",
    "accepted_run_id",
    "source_execution_id",
    "source_execution_status",
    "source_attempt"
)

acmmCampaignRunColumns <- c(
    "export_schema_version",
    "campaign_id",
    "experiment_id",
    "design_digest",
    "contract_sha256",
    "package_sha256",
    "source_execution_id",
    "source_execution_status",
    "source_execution_order",
    "source_attempt",
    "attempt",
    "source_selected",
    "selected",
    "logical_run_id",
    "run_index",
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
    "judgment_source",
    "source_result_path",
    "authoritative_result_path"
)

acmmCampaignJudgmentColumns <- c(
    "export_schema_version",
    "campaign_id",
    "experiment_id",
    "design_digest",
    "contract_sha256",
    "package_sha256",
    "source_execution_id",
    "source_execution_order",
    "source_attempt",
    "attempt",
    "source_selected",
    "selected",
    "logical_run_id",
    "run_index",
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
    "detail",
    "judgment_source"
)
