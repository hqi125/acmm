load_acmm_run_export <- function(
    exportDir,
    requireCompleteSelection = TRUE,
    populationPolicy = NULL
)
{
    acmm_require_namespace("yaml")
    acmm_require_namespace("digest")
    acmm_require_namespace("jsonlite")

    acmm_assert(
        length(exportDir) == 1 && is.character(exportDir),
        "exportDir must be one directory path.",
        "acmm_input_error"
    )
    acmm_assert(
        dir.exists(exportDir),
        paste0("Export directory does not exist: '", exportDir, "'."),
        "acmm_input_error"
    )
    exportDir <- normalizePath(exportDir, winslash = "/", mustWork = TRUE)
    if (!is.null(populationPolicy))
    {
        populationPolicy <- acmm_validate_population_policy(populationPolicy)
        requireCompleteSelection <- populationPolicy == "strict"
    }
    else
    {
        acmm_assert(
            is.logical(requireCompleteSelection) && length(requireCompleteSelection) == 1 &&
                !is.na(requireCompleteSelection),
            "requireCompleteSelection must be one non-missing logical value.",
            "acmm_analysis_api_error"
        )
        populationPolicy <- if (requireCompleteSelection) "strict" else "available-valid"
    }

    manifestPath <- file.path(exportDir, "export_manifest.yaml")
    manifest <- acmm_read_yaml_mapping(manifestPath, "export manifest")
    binding <- acmm_validate_export_manifest(manifest, exportDir)

    runsPath <- file.path(exportDir, "runs.csv")
    judgmentsPath <- file.path(exportDir, "judgments.csv")
    runs <- acmm_read_csv_character(runsPath, acmmRunColumns, "runs.csv")
    judgments <- acmm_read_csv_character(
        judgmentsPath,
        acmmJudgmentColumns,
        "judgments.csv"
    )

    expectedRunRows <- acmm_manifest_integer(manifest$tables$runs, "rows", "tables.runs")
    expectedJudgmentRows <- acmm_manifest_integer(
        manifest$tables$judgments,
        "rows",
        "tables.judgments"
    )
    acmm_assert(
        nrow(runs) == expectedRunRows,
        "runs.csv row count does not match export_manifest.yaml.",
        "acmm_integrity_error"
    )
    acmm_assert(
        nrow(judgments) == expectedJudgmentRows,
        "judgments.csv row count does not match export_manifest.yaml.",
        "acmm_integrity_error"
    )

    runs <- acmm_validate_run_table(runs, binding)
    judgments <- acmm_validate_judgment_table(judgments, runs, binding)

    selectedCount <- unique(runs$execution_selected_run_count)
    resolvedCount <- unique(runs$execution_resolved_run_count)
    if (requireCompleteSelection)
    {
        acmm_assert(
            selectedCount == resolvedCount,
            paste0(
                "Execution is incomplete: ", resolvedCount, " of ", selectedCount,
                " selected planned runs resolved."
            ),
            "acmm_incomplete_execution_error"
        )
    }

    structure(
        list(
            export_dir = exportDir,
            manifest = manifest,
            binding = binding,
            runs = runs,
            judgments = judgments,
            complete_selection = selectedCount == resolvedCount,
            population_policy = populationPolicy
        ),
        class = "acmm_export"
    )
}


load_acmm_campaign_export <- function(
    exportDir,
    requireCompleteSelection = TRUE,
    populationPolicy = NULL
)
{
    acmm_require_namespace("yaml")
    acmm_require_namespace("digest")
    acmm_require_namespace("jsonlite")

    acmm_assert(
        length(exportDir) == 1 && is.character(exportDir),
        "exportDir must be one directory path.",
        "acmm_input_error"
    )
    acmm_assert(
        dir.exists(exportDir),
        paste0("Export directory does not exist: '", exportDir, "'."),
        "acmm_input_error"
    )
    exportDir <- normalizePath(exportDir, winslash = "/", mustWork = TRUE)
    if (!is.null(populationPolicy))
    {
        populationPolicy <- acmm_validate_population_policy(populationPolicy)
        requireCompleteSelection <- populationPolicy == "strict"
    }
    else
    {
        acmm_assert(
            is.logical(requireCompleteSelection) && length(requireCompleteSelection) == 1 &&
                !is.na(requireCompleteSelection),
            "requireCompleteSelection must be one non-missing logical value.",
            "acmm_analysis_api_error"
        )
        populationPolicy <- if (requireCompleteSelection) "strict" else "available-valid"
    }

    manifestPath <- file.path(exportDir, "export_manifest.yaml")
    manifest <- acmm_read_yaml_mapping(manifestPath, "export manifest")
    binding <- acmm_validate_campaign_export_manifest(manifest, exportDir)

    plannedPath <- file.path(exportDir, "planned_runs.csv")
    runsPath <- file.path(exportDir, "runs.csv")
    judgmentsPath <- file.path(exportDir, "judgments.csv")
    planned <- acmm_read_csv_character(
        plannedPath,
        acmmCampaignPlannedRunColumns,
        "planned_runs.csv"
    )
    runs <- acmm_read_csv_character(runsPath, acmmCampaignRunColumns, "runs.csv")
    judgments <- acmm_read_csv_character(
        judgmentsPath,
        acmmCampaignJudgmentColumns,
        "judgments.csv"
    )
    toolCalls <- NULL
    toolResults <- NULL
    if (identical(binding$schema_version, acmmCampaignExportSchemaVersion))
    {
        toolCallsPath <- file.path(exportDir, "tool_calls.csv")
        toolResultsPath <- file.path(exportDir, "tool_results.csv")
        toolCalls <- acmm_read_csv_character(
            toolCallsPath,
            acmmCampaignToolCallColumns,
            "tool_calls.csv"
        )
        toolResults <- acmm_read_csv_character(
            toolResultsPath,
            acmmCampaignToolResultColumns,
            "tool_results.csv"
        )
    }

    expectedPlannedRows <- acmm_manifest_integer(
        manifest$tables$planned_runs,
        "rows",
        "tables.planned_runs"
    )
    expectedRunRows <- acmm_manifest_integer(manifest$tables$runs, "rows", "tables.runs")
    expectedJudgmentRows <- acmm_manifest_integer(
        manifest$tables$judgments,
        "rows",
        "tables.judgments"
    )
    acmm_assert(
        nrow(planned) == expectedPlannedRows,
        "planned_runs.csv row count does not match export_manifest.yaml.",
        "acmm_integrity_error"
    )
    acmm_assert(
        nrow(runs) == expectedRunRows,
        "runs.csv row count does not match export_manifest.yaml.",
        "acmm_integrity_error"
    )
    acmm_assert(
        nrow(judgments) == expectedJudgmentRows,
        "judgments.csv row count does not match export_manifest.yaml.",
        "acmm_integrity_error"
    )
    if (!is.null(toolCalls))
    {
        expectedToolCallRows <- acmm_manifest_integer(
            manifest$tables$tool_calls,
            "rows",
            "tables.tool_calls"
        )
        expectedToolResultRows <- acmm_manifest_integer(
            manifest$tables$tool_results,
            "rows",
            "tables.tool_results"
        )
        acmm_assert(
            nrow(toolCalls) == expectedToolCallRows,
            "tool_calls.csv row count does not match export_manifest.yaml.",
            "acmm_integrity_error"
        )
        acmm_assert(
            nrow(toolResults) == expectedToolResultRows,
            "tool_results.csv row count does not match export_manifest.yaml.",
            "acmm_integrity_error"
        )
    }

    planned <- acmm_validate_campaign_planned_run_table(planned, binding)
    runs <- acmm_validate_campaign_run_table(runs, planned, binding)
    judgments <- acmm_validate_campaign_judgment_table(judgments, runs, binding)
    if (!is.null(toolCalls))
    {
        toolCalls <- acmm_validate_campaign_tool_call_table(toolCalls, runs, binding)
        toolResults <- acmm_validate_campaign_tool_result_table(toolResults, runs, binding)
    }

    completeSelection <- binding$planned_run_count == binding$resolved_run_count
    if (requireCompleteSelection)
    {
        acmm_assert(
            completeSelection,
            paste0(
                "Campaign is incomplete: ", binding$resolved_run_count, " of ",
                binding$planned_run_count, " planned runs observed in the selected merge."
            ),
            "acmm_incomplete_execution_error"
        )
    }

    structure(
        list(
            export_dir = exportDir,
            manifest = manifest,
            binding = binding,
            planned_runs = planned,
            runs = runs,
            judgments = judgments,
            tool_calls = toolCalls,
            tool_results = toolResults,
            complete_selection = completeSelection,
            population_policy = populationPolicy
        ),
        class = "acmm_export"
    )
}

load_acmm_export <- function(
    exportDir,
    requireCompleteSelection = TRUE,
    populationPolicy = NULL
)
{
    acmm_assert(
        length(exportDir) == 1 && is.character(exportDir),
        "exportDir must be one directory path.",
        "acmm_input_error"
    )
    acmm_assert(
        dir.exists(exportDir),
        paste0("Export directory does not exist: '", exportDir, "'."),
        "acmm_input_error"
    )
    manifest <- acmm_read_yaml_mapping(
        file.path(exportDir, "export_manifest.yaml"),
        "export manifest"
    )
    version <- as.character(manifest$schema_version)
    if (identical(version, acmmExportSchemaVersion))
    {
        return(load_acmm_run_export(
            exportDir,
            requireCompleteSelection = requireCompleteSelection,
            populationPolicy = populationPolicy
        ))
    }
    if (version %in% acmmCampaignExportSchemaVersions)
    {
        return(load_acmm_campaign_export(
            exportDir,
            requireCompleteSelection = requireCompleteSelection,
            populationPolicy = populationPolicy
        ))
    }
    acmm_abort(
        paste0(
            "Unsupported export manifest schema_version '", version,
            "'. Supported versions: ", paste(acmmExportSchemaVersions, collapse = ", "), "."
        ),
        "acmm_schema_error"
    )
}
