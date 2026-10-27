load_acmm_export <- function(
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
