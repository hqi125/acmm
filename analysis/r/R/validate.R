acmm_validate_uuid <- function(values, label)
{
    pattern <- paste0(
        "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-",
        "[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    acmm_assert(
        all(grepl(pattern, values)),
        paste0(label, " must contain lowercase UUID-shaped values."),
        "acmm_schema_error"
    )
}

acmm_validate_sha256 <- function(values, label)
{
    acmm_assert(
        all(grepl("^[0-9a-f]{64}$", values)),
        paste0(label, " must contain lowercase SHA-256 values."),
        "acmm_schema_error"
    )
}

acmm_validate_identifier <- function(values, label, allowEmpty = FALSE)
{
    if (allowEmpty)
    {
        values <- values[values != ""]
    }

    acmm_assert(
        all(grepl("^[a-z][a-z0-9_]*$", values)),
        paste0(label, " contains an invalid ACMM identifier."),
        "acmm_schema_error"
    )
}

acmm_manifest_scalar <- function(mapping, name, label)
{
    value <- mapping[[name]]
    acmm_assert(
        !is.null(value) && length(value) == 1,
        paste0(label, " is missing scalar field '", name, "'."),
        "acmm_schema_error"
    )
    value
}

acmm_manifest_integer <- function(mapping, name, label, minimum = 0)
{
    value <- acmm_manifest_scalar(mapping, name, label)
    acmm_assert(
        is.numeric(value) && is.finite(value) && value == trunc(value) && value >= minimum,
        paste0(label, ".", name, " must be an integer >= ", minimum, "."),
        "acmm_schema_error"
    )
    as.numeric(value)
}

acmm_validate_run_export_manifest <- function(manifest, exportDir)
{
    acmm_assert(
        identical(as.character(manifest$schema_version), acmmExportSchemaVersion),
        paste0("Export manifest schema_version must be '", acmmExportSchemaVersion, "'."),
        "acmm_schema_error"
    )
    acmm_assert(is.list(manifest$source), "Export manifest source must be a mapping.")
    acmm_assert(is.list(manifest$tables), "Export manifest tables must be a mapping.")

    executionId <- as.character(
        acmm_manifest_scalar(manifest$source, "execution_id", "source")
    )
    experimentId <- as.character(
        acmm_manifest_scalar(manifest$source, "experiment_id", "source")
    )
    contractSha256 <- as.character(
        acmm_manifest_scalar(manifest$source, "contract_sha256", "source")
    )
    executionManifestSha256 <- as.character(
        acmm_manifest_scalar(manifest$source, "execution_manifest_sha256", "source")
    )

    acmm_validate_uuid(executionId, "source.execution_id")
    acmm_validate_identifier(experimentId, "source.experiment_id")
    acmm_validate_sha256(contractSha256, "source.contract_sha256")
    acmm_validate_sha256(
        executionManifestSha256,
        "source.execution_manifest_sha256"
    )

    acmm_assert(
        identical(as.character(manifest$source$contract_snapshot), "contract.yaml"),
        "source.contract_snapshot must be 'contract.yaml'.",
        "acmm_schema_error"
    )
    acmm_assert(
        identical(
            as.character(manifest$source$execution_manifest),
            "experiment_execution.yaml"
        ),
        "source.execution_manifest must be 'experiment_execution.yaml'.",
        "acmm_schema_error"
    )

    for (tableName in c("runs", "judgments"))
    {
        tableInfo <- manifest$tables[[tableName]]
        acmm_assert(
            is.list(tableInfo),
            paste0("tables.", tableName, " must be a mapping."),
            "acmm_schema_error"
        )
        expectedArtifact <- paste0(tableName, ".csv")
        artifact <- as.character(
            acmm_manifest_scalar(tableInfo, "artifact", paste0("tables.", tableName))
        )
        acmm_assert(
            identical(artifact, expectedArtifact),
            paste0("tables.", tableName, ".artifact must be '", expectedArtifact, "'."),
            "acmm_schema_error"
        )
        acmm_manifest_integer(tableInfo, "rows", paste0("tables.", tableName), 0)
        digestValue <- as.character(
            acmm_manifest_scalar(tableInfo, "sha256", paste0("tables.", tableName))
        )
        acmm_validate_sha256(digestValue, paste0("tables.", tableName, ".sha256"))

        artifactPath <- file.path(exportDir, artifact)
        acmm_assert(
            file.exists(artifactPath),
            paste0("Export artifact is missing: '", artifactPath, "'."),
            "acmm_input_error"
        )
        actualDigest <- acmm_sha256_file(artifactPath)
        acmm_assert(
            identical(actualDigest, digestValue),
            paste0("SHA-256 mismatch for export artifact '", artifact, "'."),
            "acmm_integrity_error"
        )
    }

    list(
        execution_id = executionId,
        experiment_id = experimentId,
        contract_sha256 = contractSha256
    )
}

acmm_validate_run_table <- function(runs, binding)
{
    rowCount <- nrow(runs)
    acmm_assert(rowCount > 0, "runs.csv must contain at least one realized attempt.")

    acmm_assert(
        all(runs$export_schema_version == acmmExportSchemaVersion),
        "runs.csv export_schema_version does not match the supported schema.",
        "acmm_schema_error"
    )
    acmm_assert(
        all(runs$execution_id == binding$execution_id),
        "runs.csv execution_id does not match export_manifest.yaml.",
        "acmm_integrity_error"
    )
    acmm_assert(
        all(runs$experiment_id == binding$experiment_id),
        "runs.csv experiment_id does not match export_manifest.yaml.",
        "acmm_integrity_error"
    )
    acmm_assert(
        all(runs$contract_sha256 == binding$contract_sha256),
        "runs.csv contract_sha256 does not match export_manifest.yaml.",
        "acmm_integrity_error"
    )

    runs$execution_contract_run_count <- acmm_parse_integer(
        runs$execution_contract_run_count,
        "execution_contract_run_count",
        1
    )
    runs$execution_selected_run_count <- acmm_parse_integer(
        runs$execution_selected_run_count,
        "execution_selected_run_count",
        1
    )
    runs$execution_resolved_run_count <- acmm_parse_integer(
        runs$execution_resolved_run_count,
        "execution_resolved_run_count",
        0
    )
    runs$max_attempts_per_run <- acmm_parse_integer(
        runs$max_attempts_per_run,
        "max_attempts_per_run",
        1
    )
    runs$inter_run_delay_seconds <- acmm_parse_number(
        runs$inter_run_delay_seconds,
        "inter_run_delay_seconds",
        0
    )
    runs$run_index <- acmm_parse_integer(runs$run_index, "run_index", 0)
    runs$attempt <- acmm_parse_integer(runs$attempt, "attempt", 1)
    runs$selected <- acmm_parse_boolean(runs$selected, "selected")
    runs$repetition <- acmm_parse_integer(runs$repetition, "repetition", 1)
    runs$started_at <- acmm_parse_timestamp(runs$started_at, "started_at")
    runs$ended_at <- acmm_parse_timestamp(runs$ended_at, "ended_at")
    runs$evidence_count <- acmm_parse_integer(runs$evidence_count, "evidence_count", 0)
    runs$judgment_count <- acmm_parse_integer(runs$judgment_count, "judgment_count", 0)

    acmm_validate_uuid(runs$run_id, "runs.csv run_id")
    acmm_validate_identifier(runs$subject_id, "runs.csv subject_id")
    acmm_validate_identifier(runs$scenario_id, "runs.csv scenario_id")
    acmm_assert(
        all(runs$termination %in% acmmTerminationValues),
        "runs.csv contains an unknown termination value.",
        "acmm_schema_error"
    )
    acmm_assert(
        all(runs$validity %in% acmmValidityValues),
        "runs.csv contains an unknown validity value.",
        "acmm_schema_error"
    )
    acmm_assert(
        all(runs$ended_at >= runs$started_at),
        "runs.csv contains a run whose ended_at precedes started_at.",
        "acmm_schema_error"
    )
    acmm_assert(
        !anyDuplicated(runs$run_id),
        "runs.csv contains duplicate run_id values.",
        "acmm_schema_error"
    )
    acmm_assert(
        !anyDuplicated(runs$result_path),
        "runs.csv contains duplicate result_path values.",
        "acmm_schema_error"
    )

    acmm_validate_json_object_column(
        runs$subject_configuration_json,
        "subject_configuration_json"
    )
    acmm_validate_json_object_column(
        runs$environment_configuration_json,
        "environment_configuration_json"
    )

    scalarColumns <- c(
        "execution_contract_run_count",
        "execution_selected_run_count",
        "execution_resolved_run_count",
        "max_attempts_per_run",
        "inter_run_delay_seconds"
    )
    for (columnName in scalarColumns)
    {
        acmm_assert(
            length(unique(runs[[columnName]])) == 1,
            paste0("runs.csv column '", columnName, "' must be execution-constant."),
            "acmm_integrity_error"
        )
    }

    selectedCount <- sum(runs$selected)
    acmm_assert(
        selectedCount == unique(runs$execution_resolved_run_count),
        "Selected run count does not match execution_resolved_run_count.",
        "acmm_integrity_error"
    )
    acmm_assert(
        length(unique(runs$run_index)) == unique(runs$execution_selected_run_count),
        "Realized slot count does not match execution_selected_run_count.",
        "acmm_integrity_error"
    )
    acmm_assert(
        unique(runs$execution_resolved_run_count) <= unique(runs$execution_selected_run_count),
        "Resolved run count cannot exceed selected run count.",
        "acmm_integrity_error"
    )
    acmm_assert(
        unique(runs$execution_selected_run_count) <= unique(runs$execution_contract_run_count),
        "Selected run count cannot exceed Contract run count.",
        "acmm_integrity_error"
    )

    acmm_assert(
        all(!runs$selected | runs$validity == "valid"),
        "A selected run must be scientifically valid.",
        "acmm_integrity_error"
    )
    acmm_assert(
        all(runs$selected | runs$validity == "invalid"),
        "A scientifically valid unselected retry is not permitted.",
        "acmm_integrity_error"
    )
    invalidOnlyTerminations <- c("provider_error", "harness_error")
    acmm_assert(
        all(!(runs$termination %in% invalidOnlyTerminations) | runs$validity == "invalid"),
        "provider_error and harness_error terminations must be invalid.",
        "acmm_integrity_error"
    )

    validFailure <- runs$failure_kind == "" & runs$failure_message == ""
    invalidFailure <- runs$failure_kind != "" & runs$failure_message != ""
    acmm_assert(
        all(ifelse(runs$validity == "valid", validFailure, invalidFailure)),
        "Run failure fields are inconsistent with execution validity.",
        "acmm_integrity_error"
    )

    slotKeys <- sort(unique(runs$run_index))
    for (runIndex in slotKeys)
    {
        slotRows <- which(runs$run_index == runIndex)
        attempts <- runs$attempt[slotRows]
        expectedAttempts <- seq_len(length(attempts))
        acmm_assert(
            all(attempts == expectedAttempts),
            paste0("Attempts for run_index ", runIndex, " must be contiguous from 1."),
            "acmm_integrity_error"
        )
        acmm_assert(
            length(slotRows) <= unique(runs$max_attempts_per_run),
            paste0("run_index ", runIndex, " exceeds max_attempts_per_run."),
            "acmm_integrity_error"
        )

        selectedRows <- slotRows[runs$selected[slotRows]]
        acmm_assert(
            length(selectedRows) <= 1,
            paste0("run_index ", runIndex, " has multiple selected attempts."),
            "acmm_integrity_error"
        )
        if (length(selectedRows) == 1)
        {
            acmm_assert(
                selectedRows == tail(slotRows, 1),
                paste0("run_index ", runIndex, " selected attempt must be final."),
                "acmm_integrity_error"
            )
        }
    }

    subjectGroups <- split(seq_len(rowCount), runs$subject_id)
    subjectColumns <- c(
        "agent_name",
        "agent_version",
        "model_name",
        "model_provider"
    )
    for (subjectRows in subjectGroups)
    {
        validSubjectRows <- subjectRows[runs$validity[subjectRows] == "valid"]
        for (columnName in subjectColumns)
        {
            values <- runs[[columnName]][subjectRows]
            populatedValues <- unique(values[nzchar(values)])
            acmm_assert(
                length(populatedValues) <= 1,
                paste0(
                    "Subject provenance column '", columnName,
                    "' has conflicting populated values within one subject_id."
                ),
                "acmm_integrity_error"
            )

            if (length(validSubjectRows) > 0)
            {
                validValues <- runs[[columnName]][validSubjectRows]
                acmm_assert(
                    all(nzchar(validValues)) && length(unique(validValues)) == 1,
                    paste0(
                        "Subject provenance column '", columnName,
                        "' must be populated and consistent across valid runs."
                    ),
                    "acmm_integrity_error"
                )
            }
        }
    }

    runs
}

acmm_validate_judgment_table <- function(judgments, runs, binding)
{
    acmm_assert(
        all(judgments$export_schema_version == acmmExportSchemaVersion),
        "judgments.csv export_schema_version does not match the supported schema.",
        "acmm_schema_error"
    )
    acmm_assert(
        all(judgments$execution_id == binding$execution_id),
        "judgments.csv execution_id does not match export_manifest.yaml.",
        "acmm_integrity_error"
    )
    acmm_assert(
        all(judgments$experiment_id == binding$experiment_id),
        "judgments.csv experiment_id does not match export_manifest.yaml.",
        "acmm_integrity_error"
    )
    acmm_assert(
        all(judgments$contract_sha256 == binding$contract_sha256),
        "judgments.csv contract_sha256 does not match export_manifest.yaml.",
        "acmm_integrity_error"
    )

    judgments$run_index <- acmm_parse_integer(judgments$run_index, "run_index", 0)
    judgments$attempt <- acmm_parse_integer(judgments$attempt, "attempt", 1)
    judgments$selected <- acmm_parse_boolean(judgments$selected, "selected")
    judgments$repetition <- acmm_parse_integer(judgments$repetition, "repetition", 1)

    acmm_validate_uuid(judgments$run_id, "judgments.csv run_id")
    acmm_validate_identifier(judgments$subject_id, "judgments.csv subject_id")
    acmm_validate_identifier(judgments$scenario_id, "judgments.csv scenario_id")
    acmm_validate_identifier(judgments$opportunity_id, "judgments.csv opportunity_id")
    acmm_validate_identifier(judgments$requirement_id, "judgments.csv requirement_id")
    acmm_validate_identifier(judgments$dimension, "judgments.csv dimension")
    acmm_validate_identifier(judgments$check_id, "judgments.csv check_id")
    acmm_validate_identifier(judgments$evaluator, "judgments.csv evaluator")

    acmm_assert(
        all(judgments$validity %in% acmmValidityValues),
        "judgments.csv contains an unknown validity value.",
        "acmm_schema_error"
    )
    acmm_assert(
        all(judgments$termination %in% acmmTerminationValues),
        "judgments.csv contains an unknown termination value.",
        "acmm_schema_error"
    )
    acmm_assert(
        all(judgments$outcome %in% acmmOutcomeValues),
        "judgments.csv contains an unknown judgment outcome.",
        "acmm_schema_error"
    )
    acmm_validate_json_string_array_column(
        judgments$evidence_ids_json,
        "evidence_ids_json"
    )

    runLookup <- match(judgments$run_id, runs$run_id)
    acmm_assert(
        all(!is.na(runLookup)),
        "judgments.csv references a run_id absent from runs.csv.",
        "acmm_integrity_error"
    )

    bindingColumns <- c(
        "run_index",
        "attempt",
        "selected",
        "subject_id",
        "scenario_id",
        "repetition",
        "validity",
        "termination"
    )
    for (columnName in bindingColumns)
    {
        acmm_assert(
            identical(judgments[[columnName]], runs[[columnName]][runLookup]),
            paste0(
                "judgments.csv column '", columnName,
                "' does not agree with runs.csv for referenced runs."
            ),
            "acmm_integrity_error"
        )
    }

    judgmentKeys <- paste(judgments$run_id, judgments$opportunity_id, sep = "\037")
    acmm_assert(
        !anyDuplicated(judgmentKeys),
        "A run contains duplicate judgments for one opportunity_id.",
        "acmm_integrity_error"
    )

    actualCounts <- table(factor(judgments$run_id, levels = runs$run_id))
    acmm_assert(
        identical(as.numeric(actualCounts), runs$judgment_count),
        "runs.csv judgment_count does not match judgments.csv rows.",
        "acmm_integrity_error"
    )

    requirementDimensions <- split(judgments$dimension, judgments$requirement_id)
    acmm_assert(
        all(vapply(requirementDimensions, function(values) length(unique(values)) == 1,
                   logical(1))),
        "One requirement_id maps to multiple dimensions.",
        "acmm_integrity_error"
    )

    checkEvaluators <- split(judgments$evaluator, judgments$check_id)
    acmm_assert(
        all(vapply(checkEvaluators, function(values) length(unique(values)) == 1,
                   logical(1))),
        "One check_id maps to multiple evaluators.",
        "acmm_integrity_error"
    )

    opportunityKeys <- paste(
        judgments$scenario_id,
        judgments$opportunity_id,
        sep = "\037"
    )
    opportunityRows <- split(seq_len(nrow(judgments)), opportunityKeys)
    for (rows in opportunityRows)
    {
        acmm_assert(
            length(unique(judgments$requirement_id[rows])) == 1 &&
                length(unique(judgments$check_id[rows])) == 1,
            "One scenario opportunity maps to inconsistent requirement/check metadata.",
            "acmm_integrity_error"
        )
    }

    judgments
}


acmm_validate_export_manifest <- function(manifest, exportDir)
{
    version <- as.character(manifest$schema_version)
    if (identical(version, acmmExportSchemaVersion))
    {
        binding <- acmm_validate_run_export_manifest(manifest, exportDir)
        binding$source_kind <- "run"
        binding$schema_version <- version
        return(binding)
    }
    if (identical(version, acmmCampaignExportSchemaVersion))
    {
        return(acmm_validate_campaign_export_manifest(manifest, exportDir))
    }
    acmm_abort(
        paste0(
            "Unsupported export manifest schema_version '", version,
            "'. Supported versions: ", paste(acmmExportSchemaVersions, collapse = ", "), "."
        ),
        "acmm_schema_error"
    )
}

acmm_validate_campaign_export_manifest <- function(manifest, exportDir)
{
    acmm_assert(
        identical(as.character(manifest$schema_version), acmmCampaignExportSchemaVersion),
        paste0(
            "Campaign export manifest schema_version must be '",
            acmmCampaignExportSchemaVersion, "'."
        ),
        "acmm_schema_error"
    )
    acmm_assert(is.list(manifest$source), "Campaign export manifest source must be a mapping.")
    acmm_assert(is.list(manifest$tables), "Campaign export manifest tables must be a mapping.")
    acmm_assert(is.list(manifest$executions), "Campaign export manifest executions must be a list.")

    source <- manifest$source
    acmm_assert(
        identical(as.character(source$kind), "campaign"),
        "Campaign export source.kind must be 'campaign'.",
        "acmm_schema_error"
    )
    campaignId <- as.character(acmm_manifest_scalar(source, "campaign_id", "source"))
    experimentId <- as.character(acmm_manifest_scalar(source, "experiment_id", "source"))
    designDigest <- as.character(acmm_manifest_scalar(source, "design_digest", "source"))
    packageSha256 <- as.character(acmm_manifest_scalar(source, "package_sha256", "source"))
    contractSha256 <- as.character(acmm_manifest_scalar(source, "contract_sha256", "source"))
    mergeId <- as.character(acmm_manifest_scalar(source, "merge_id", "source"))
    campaignManifestSha256 <- as.character(
        acmm_manifest_scalar(source, "campaign_manifest_sha256", "source")
    )
    mergeManifestSha256 <- as.character(
        acmm_manifest_scalar(source, "merge_manifest_sha256", "source")
    )
    acmm_assert(nzchar(campaignId), "source.campaign_id must be non-empty.", "acmm_schema_error")
    acmm_assert(nzchar(mergeId), "source.merge_id must be non-empty.", "acmm_schema_error")
    acmm_validate_identifier(experimentId, "source.experiment_id")
    acmm_validate_sha256(designDigest, "source.design_digest")
    acmm_validate_sha256(packageSha256, "source.package_sha256")
    acmm_validate_sha256(contractSha256, "source.contract_sha256")
    acmm_validate_sha256(campaignManifestSha256, "source.campaign_manifest_sha256")
    acmm_validate_sha256(mergeManifestSha256, "source.merge_manifest_sha256")
    acmm_assert(
        identical(as.character(source$campaign_manifest), "campaign.json"),
        "source.campaign_manifest must be 'campaign.json'.",
        "acmm_schema_error"
    )
    acmm_assert(
        identical(as.character(source$merge_manifest), "campaign_merge.json"),
        "source.merge_manifest must be 'campaign_merge.json'.",
        "acmm_schema_error"
    )
    acmm_assert(
        as.character(source$merge_completeness) %in% c("complete", "partial"),
        "source.merge_completeness must be 'complete' or 'partial'.",
        "acmm_schema_error"
    )
    acmm_assert(is.list(source$merge_population), "source.merge_population must be a mapping.")
    plannedRunCount <- acmm_manifest_integer(source$merge_population, "total", "source.merge_population", 1)
    resolvedRunCount <- acmm_manifest_integer(source$merge_population, "observed", "source.merge_population", 0)
    pendingRunCount <- acmm_manifest_integer(source$merge_population, "pending", "source.merge_population", 0)
    acmm_assert(
        resolvedRunCount + pendingRunCount == plannedRunCount,
        "source.merge_population counts are inconsistent.",
        "acmm_integrity_error"
    )
    expectedCompleteness <- if (pendingRunCount == 0) "complete" else "partial"
    acmm_assert(
        identical(as.character(source$merge_completeness), expectedCompleteness),
        "source.merge_completeness does not agree with source.merge_population.",
        "acmm_integrity_error"
    )

    expectedTables <- c("planned_runs", "runs", "judgments")
    for (tableName in expectedTables)
    {
        tableInfo <- manifest$tables[[tableName]]
        acmm_assert(
            is.list(tableInfo),
            paste0("tables.", tableName, " must be a mapping."),
            "acmm_schema_error"
        )
        expectedArtifact <- paste0(tableName, ".csv")
        artifact <- as.character(
            acmm_manifest_scalar(tableInfo, "artifact", paste0("tables.", tableName))
        )
        acmm_assert(
            identical(artifact, expectedArtifact),
            paste0("tables.", tableName, ".artifact must be '", expectedArtifact, "'."),
            "acmm_schema_error"
        )
        acmm_manifest_integer(tableInfo, "rows", paste0("tables.", tableName), 0)
        digestValue <- as.character(
            acmm_manifest_scalar(tableInfo, "sha256", paste0("tables.", tableName))
        )
        acmm_validate_sha256(digestValue, paste0("tables.", tableName, ".sha256"))
        artifactPath <- file.path(exportDir, artifact)
        acmm_assert(
            file.exists(artifactPath),
            paste0("Export artifact is missing: '", artifactPath, "'."),
            "acmm_input_error"
        )
        acmm_assert(
            identical(acmm_sha256_file(artifactPath), digestValue),
            paste0("SHA-256 mismatch for export artifact '", artifact, "'."),
            "acmm_integrity_error"
        )
    }
    acmm_assert(
        acmm_manifest_integer(manifest$tables$planned_runs, "rows", "tables.planned_runs", 0) == plannedRunCount,
        "planned_runs.csv row count must equal source.merge_population.total.",
        "acmm_integrity_error"
    )

    list(
        source_kind = "campaign",
        schema_version = acmmCampaignExportSchemaVersion,
        campaign_id = campaignId,
        merge_id = mergeId,
        experiment_id = experimentId,
        design_digest = designDigest,
        package_sha256 = packageSha256,
        contract_sha256 = contractSha256,
        planned_run_count = plannedRunCount,
        resolved_run_count = resolvedRunCount
    )
}

acmm_validate_campaign_planned_run_table <- function(planned, binding)
{
    acmm_assert(
        nrow(planned) == binding$planned_run_count,
        "planned_runs.csv row count does not match the Campaign population.",
        "acmm_integrity_error"
    )
    if (nrow(planned) == 0)
    {
        return(planned)
    }
    acmm_assert(
        all(planned$export_schema_version == acmmCampaignExportSchemaVersion),
        "planned_runs.csv export_schema_version does not match the Campaign export schema.",
        "acmm_schema_error"
    )
    identityChecks <- list(
        campaign_id = binding$campaign_id,
        experiment_id = binding$experiment_id,
        design_digest = binding$design_digest,
        contract_sha256 = binding$contract_sha256,
        package_sha256 = binding$package_sha256
    )
    for (columnName in names(identityChecks))
    {
        acmm_assert(
            all(planned[[columnName]] == identityChecks[[columnName]]),
            paste0("planned_runs.csv column '", columnName, "' does not match export_manifest.yaml."),
            "acmm_integrity_error"
        )
    }
    planned$run_index <- acmm_parse_integer(planned$run_index, "run_index", 0)
    planned$repetition <- acmm_parse_integer(planned$repetition, "repetition", 1)
    planned$observed <- acmm_parse_boolean(planned$observed, "observed")
    acmm_validate_identifier(planned$logical_run_id, "planned_runs.csv logical_run_id")
    acmm_validate_identifier(planned$subject_id, "planned_runs.csv subject_id")
    acmm_validate_identifier(planned$scenario_id, "planned_runs.csv scenario_id")
    acmm_assert(!anyDuplicated(planned$run_index), "planned_runs.csv contains duplicate run_index values.", "acmm_integrity_error")
    acmm_assert(!anyDuplicated(planned$logical_run_id), "planned_runs.csv contains duplicate logical_run_id values.", "acmm_integrity_error")
    acmm_assert(
        all(sort(planned$run_index) == seq.int(0, nrow(planned) - 1)),
        "planned_runs.csv run_index must form a contiguous zero-based Campaign population.",
        "acmm_integrity_error"
    )
    acmm_assert(
        sum(planned$observed) == binding$resolved_run_count,
        "planned_runs.csv observed count does not match the merge population.",
        "acmm_integrity_error"
    )
    acmm_assert(
        all(ifelse(
            planned$observed,
            nzchar(planned$accepted_run_id) & nzchar(planned$source_execution_id) &
                nzchar(planned$source_execution_status) & nzchar(planned$source_attempt),
            !nzchar(planned$accepted_run_id) & !nzchar(planned$source_execution_id) &
                !nzchar(planned$source_execution_status) & !nzchar(planned$source_attempt)
        )),
        "planned_runs.csv accepted-observation provenance is inconsistent with observed.",
        "acmm_integrity_error"
    )
    planned
}

acmm_validate_campaign_run_table <- function(runs, planned, binding)
{
    if (nrow(runs) == 0)
    {
        acmm_assert(
            binding$resolved_run_count == 0,
            "Campaign export has no realized attempts but reports observed runs.",
            "acmm_integrity_error"
        )
        return(runs)
    }
    acmm_assert(
        all(runs$export_schema_version == acmmCampaignExportSchemaVersion),
        "runs.csv export_schema_version does not match the Campaign export schema.",
        "acmm_schema_error"
    )
    identityChecks <- list(
        campaign_id = binding$campaign_id,
        experiment_id = binding$experiment_id,
        design_digest = binding$design_digest,
        contract_sha256 = binding$contract_sha256,
        package_sha256 = binding$package_sha256
    )
    for (columnName in names(identityChecks))
    {
        acmm_assert(
            all(runs[[columnName]] == identityChecks[[columnName]]),
            paste0("runs.csv column '", columnName, "' does not match export_manifest.yaml."),
            "acmm_integrity_error"
        )
    }

    runs$source_execution_order <- acmm_parse_integer(runs$source_execution_order, "source_execution_order", 1)
    runs$source_attempt <- acmm_parse_integer(runs$source_attempt, "source_attempt", 1)
    runs$attempt <- acmm_parse_integer(runs$attempt, "attempt", 1)
    runs$source_selected <- acmm_parse_boolean(runs$source_selected, "source_selected")
    runs$selected <- acmm_parse_boolean(runs$selected, "selected")
    runs$run_index <- acmm_parse_integer(runs$run_index, "run_index", 0)
    runs$repetition <- acmm_parse_integer(runs$repetition, "repetition", 1)
    runs$started_at <- acmm_parse_timestamp(runs$started_at, "started_at")
    runs$ended_at <- acmm_parse_timestamp(runs$ended_at, "ended_at")
    runs$evidence_count <- acmm_parse_integer(runs$evidence_count, "evidence_count", 0)
    runs$judgment_count <- acmm_parse_integer(runs$judgment_count, "judgment_count", 0)

    acmm_validate_uuid(runs$run_id, "runs.csv run_id")
    acmm_validate_identifier(runs$logical_run_id, "runs.csv logical_run_id")
    acmm_validate_identifier(runs$subject_id, "runs.csv subject_id")
    acmm_validate_identifier(runs$scenario_id, "runs.csv scenario_id")
    acmm_assert(all(runs$termination %in% acmmTerminationValues), "runs.csv contains an unknown termination value.", "acmm_schema_error")
    acmm_assert(all(runs$validity %in% acmmValidityValues), "runs.csv contains an unknown validity value.", "acmm_schema_error")
    acmm_assert(all(runs$source_execution_status %in% c("completed", "interrupted", "interrupted_unclean", "failed")), "runs.csv contains an unknown Campaign Execution status.", "acmm_schema_error")
    acmm_assert(all(runs$judgment_source %in% c("source", "merge")), "runs.csv contains an unknown judgment_source.", "acmm_schema_error")
    acmm_assert(all(runs$ended_at >= runs$started_at), "runs.csv contains a run whose ended_at precedes started_at.", "acmm_schema_error")
    acmm_assert(!anyDuplicated(runs$run_id), "runs.csv contains duplicate run_id values.", "acmm_integrity_error")
    acmm_assert(!anyDuplicated(runs$source_result_path), "runs.csv contains duplicate source_result_path values.", "acmm_integrity_error")
    acmm_validate_json_object_column(runs$subject_configuration_json, "subject_configuration_json")
    acmm_validate_json_object_column(runs$environment_configuration_json, "environment_configuration_json")

    plannedLookup <- match(runs$run_index, planned$run_index)
    acmm_assert(all(!is.na(plannedLookup)), "runs.csv references a run_index absent from planned_runs.csv.", "acmm_integrity_error")
    for (columnName in c("logical_run_id", "subject_id", "scenario_id", "repetition"))
    {
        acmm_assert(
            identical(runs[[columnName]], planned[[columnName]][plannedLookup]),
            paste0("runs.csv column '", columnName, "' does not match planned_runs.csv."),
            "acmm_integrity_error"
        )
    }

    logicalIds <- unique(runs$logical_run_id)
    for (logicalRunId in logicalIds)
    {
        slotRows <- which(runs$logical_run_id == logicalRunId)
        slotRows <- slotRows[order(runs$attempt[slotRows])]
        acmm_assert(
            all(runs$attempt[slotRows] == seq_len(length(slotRows))),
            paste0("Campaign attempts for logical_run_id ", logicalRunId, " must be contiguous from 1."),
            "acmm_integrity_error"
        )
        selectedRows <- slotRows[runs$selected[slotRows]]
        acmm_assert(length(selectedRows) <= 1, paste0("logical_run_id ", logicalRunId, " has multiple selected attempts."), "acmm_integrity_error")
        if (length(selectedRows) == 1)
        {
            acmm_assert(selectedRows == tail(slotRows, 1), paste0("logical_run_id ", logicalRunId, " selected attempt must be final."), "acmm_integrity_error")
        }
    }
    sourceKeys <- paste(runs$source_execution_id, runs$logical_run_id, sep = "\037")
    sourceGroups <- split(seq_len(nrow(runs)), sourceKeys, drop = TRUE)
    for (sourceRows in sourceGroups)
    {
        sourceRows <- sourceRows[order(runs$source_attempt[sourceRows])]
        acmm_assert(
            all(runs$source_attempt[sourceRows] == seq_len(length(sourceRows))),
            "source_attempt must be contiguous from 1 within each Campaign Execution logical slot.",
            "acmm_integrity_error"
        )
        sourceSelected <- sourceRows[runs$source_selected[sourceRows]]
        acmm_assert(length(sourceSelected) <= 1, "A source Campaign Execution logical slot has multiple selected attempts.", "acmm_integrity_error")
        if (length(sourceSelected) == 1)
        {
            acmm_assert(sourceSelected == tail(sourceRows, 1), "source_selected attempt must be final within its source Campaign Execution slot.", "acmm_integrity_error")
        }
    }

    acmm_assert(all(!runs$selected | runs$validity == "valid"), "A merge-selected Campaign run must be scientifically valid.", "acmm_integrity_error")
    acmm_assert(all(!runs$selected | runs$source_selected), "A merge-selected Campaign run must be selected by its source execution.", "acmm_integrity_error")
    acmm_assert(all((runs$judgment_source == "merge") == runs$selected), "runs.csv judgment_source must be 'merge' exactly for selected observations.", "acmm_integrity_error")
    acmm_assert(all((nzchar(runs$authoritative_result_path)) == runs$selected), "runs.csv authoritative_result_path must be populated exactly for selected observations.", "acmm_integrity_error")
    acmm_assert(sum(runs$selected) == binding$resolved_run_count, "Selected Campaign run count does not match the merge population.", "acmm_integrity_error")

    observedPlanned <- planned[planned$observed, , drop = FALSE]
    selectedRuns <- runs[runs$selected, , drop = FALSE]
    selectedLookup <- match(observedPlanned$logical_run_id, selectedRuns$logical_run_id)
    acmm_assert(all(!is.na(selectedLookup)), "An observed planned Campaign run is absent from runs.csv.", "acmm_integrity_error")
    acmm_assert(
        identical(observedPlanned$accepted_run_id, selectedRuns$run_id[selectedLookup]),
        "planned_runs.csv accepted_run_id does not match runs.csv selected observations.",
        "acmm_integrity_error"
    )
    acmm_assert(
        identical(
            observedPlanned$source_execution_id,
            selectedRuns$source_execution_id[selectedLookup]
        ) && identical(
            observedPlanned$source_execution_status,
            selectedRuns$source_execution_status[selectedLookup]
        ) && identical(
            observedPlanned$source_attempt,
            as.character(selectedRuns$source_attempt[selectedLookup])
        ),
        "planned_runs.csv source provenance does not match runs.csv selected observations.",
        "acmm_integrity_error"
    )
    runs
}

acmm_validate_campaign_judgment_table <- function(judgments, runs, binding)
{
    if (nrow(judgments) == 0)
    {
        acmm_assert(
            sum(runs$judgment_count) == 0,
            "runs.csv judgment_count is non-zero but judgments.csv is empty.",
            "acmm_integrity_error"
        )
        return(judgments)
    }
    acmm_assert(
        all(judgments$export_schema_version == acmmCampaignExportSchemaVersion),
        "judgments.csv export_schema_version does not match the Campaign export schema.",
        "acmm_schema_error"
    )
    identityChecks <- list(
        campaign_id = binding$campaign_id,
        experiment_id = binding$experiment_id,
        design_digest = binding$design_digest,
        contract_sha256 = binding$contract_sha256,
        package_sha256 = binding$package_sha256
    )
    for (columnName in names(identityChecks))
    {
        acmm_assert(
            all(judgments[[columnName]] == identityChecks[[columnName]]),
            paste0("judgments.csv column '", columnName, "' does not match export_manifest.yaml."),
            "acmm_integrity_error"
        )
    }
    judgments$source_execution_order <- acmm_parse_integer(judgments$source_execution_order, "source_execution_order", 1)
    judgments$source_attempt <- acmm_parse_integer(judgments$source_attempt, "source_attempt", 1)
    judgments$attempt <- acmm_parse_integer(judgments$attempt, "attempt", 1)
    judgments$source_selected <- acmm_parse_boolean(judgments$source_selected, "source_selected")
    judgments$selected <- acmm_parse_boolean(judgments$selected, "selected")
    judgments$run_index <- acmm_parse_integer(judgments$run_index, "run_index", 0)
    judgments$repetition <- acmm_parse_integer(judgments$repetition, "repetition", 1)
    acmm_validate_uuid(judgments$run_id, "judgments.csv run_id")
    acmm_validate_identifier(judgments$logical_run_id, "judgments.csv logical_run_id")
    acmm_validate_identifier(judgments$subject_id, "judgments.csv subject_id")
    acmm_validate_identifier(judgments$scenario_id, "judgments.csv scenario_id")
    acmm_validate_identifier(judgments$opportunity_id, "judgments.csv opportunity_id")
    acmm_validate_identifier(judgments$requirement_id, "judgments.csv requirement_id")
    acmm_validate_identifier(judgments$dimension, "judgments.csv dimension")
    acmm_validate_identifier(judgments$check_id, "judgments.csv check_id")
    acmm_validate_identifier(judgments$evaluator, "judgments.csv evaluator")
    acmm_assert(all(judgments$validity %in% acmmValidityValues), "judgments.csv contains an unknown validity value.", "acmm_schema_error")
    acmm_assert(all(judgments$termination %in% acmmTerminationValues), "judgments.csv contains an unknown termination value.", "acmm_schema_error")
    acmm_assert(all(judgments$outcome %in% acmmOutcomeValues), "judgments.csv contains an unknown judgment outcome.", "acmm_schema_error")
    acmm_assert(all(judgments$judgment_source %in% c("source", "merge")), "judgments.csv contains an unknown judgment_source.", "acmm_schema_error")
    acmm_validate_json_string_array_column(judgments$evidence_ids_json, "evidence_ids_json")

    runLookup <- match(judgments$run_id, runs$run_id)
    acmm_assert(all(!is.na(runLookup)), "judgments.csv references a run_id absent from runs.csv.", "acmm_integrity_error")
    bindingColumns <- c(
        "source_execution_id", "source_execution_order", "source_attempt", "attempt",
        "source_selected", "selected", "logical_run_id", "run_index", "subject_id",
        "scenario_id", "repetition", "validity", "termination", "judgment_source"
    )
    for (columnName in bindingColumns)
    {
        acmm_assert(
            identical(judgments[[columnName]], runs[[columnName]][runLookup]),
            paste0("judgments.csv column '", columnName, "' does not agree with runs.csv."),
            "acmm_integrity_error"
        )
    }
    judgmentKeys <- paste(judgments$run_id, judgments$opportunity_id, sep = "\037")
    acmm_assert(!anyDuplicated(judgmentKeys), "A run contains duplicate judgments for one opportunity_id.", "acmm_integrity_error")
    actualCounts <- table(factor(judgments$run_id, levels = runs$run_id))
    acmm_assert(
        identical(as.numeric(actualCounts), runs$judgment_count),
        "runs.csv judgment_count does not match judgments.csv rows.",
        "acmm_integrity_error"
    )
    requirementDimensions <- split(judgments$dimension, judgments$requirement_id)
    acmm_assert(
        all(vapply(requirementDimensions, function(values) length(unique(values)) == 1, logical(1))),
        "One requirement_id maps to multiple dimensions.",
        "acmm_integrity_error"
    )
    checkEvaluators <- split(judgments$evaluator, judgments$check_id)
    acmm_assert(
        all(vapply(checkEvaluators, function(values) length(unique(values)) == 1, logical(1))),
        "One check_id maps to multiple evaluators.",
        "acmm_integrity_error"
    )
    judgments
}
