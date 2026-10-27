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

acmm_validate_export_manifest <- function(manifest, exportDir)
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
    acmm_assert(
        all(runs$termination != "harness_error" | runs$validity == "invalid"),
        "harness_error termination must be invalid.",
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
