acmm_stack_estimator_tables <- function(tables)
{
    acmm_assert(
        is.list(tables) && length(tables) > 0 && !is.null(names(tables)),
        "Analysis tables must be a named non-empty list.",
        "acmm_analysis_api_error"
    )

    rows <- list()
    rowIndex <- 1
    for (estimatorName in names(tables))
    {
        table <- tables[[estimatorName]]
        acmm_assert(
            is.data.frame(table),
            paste0("Analysis table '", estimatorName, "' must be a data frame."),
            "acmm_analysis_api_error"
        )
        if (nrow(table) == 0)
        {
            next
        }

        table$estimand <- estimatorName
        table <- table[c("estimand", setdiff(names(table), "estimand"))]
        rows[[rowIndex]] <- table
        rowIndex <- rowIndex + 1
    }

    if (length(rows) == 0)
    {
        return(data.frame())
    }

    columnNames <- unique(unlist(lapply(rows, names), use.names = FALSE))
    alignedRows <- lapply(rows, function(table)
    {
        missingColumns <- setdiff(columnNames, names(table))
        for (columnName in missingColumns)
        {
            table[[columnName]] <- NA
        }
        table[columnNames]
    })

    result <- do.call(rbind, alignedRows)
    rownames(result) <- NULL
    result
}

write_acmm_analysis_csv <- function(tables, outputPath)
{
    acmm_assert(
        is.character(outputPath) && length(outputPath) == 1 &&
            !is.na(outputPath) && nzchar(outputPath),
        "outputPath must be one non-empty string.",
        "acmm_analysis_api_error"
    )

    outputDir <- dirname(outputPath)
    if (!dir.exists(outputDir))
    {
        dir.create(outputDir, recursive = TRUE, showWarnings = FALSE)
    }

    table <- acmm_stack_estimator_tables(tables)
    utils::write.csv(
        table,
        file = outputPath,
        row.names = FALSE,
        na = ""
    )
    invisible(outputPath)
}

write_acmm_population_tables <- function(population, outputDir)
{
    acmm_validate_population(population)
    acmm_assert(
        is.character(outputDir) && length(outputDir) == 1 &&
            !is.na(outputDir) && nzchar(outputDir),
        "outputDir must be one non-empty string.",
        "acmm_analysis_api_error"
    )

    if (!dir.exists(outputDir))
    {
        dir.create(outputDir, recursive = TRUE, showWarnings = FALSE)
    }

    utils::write.csv(
        population$summary,
        file = file.path(outputDir, "population_summary.csv"),
        row.names = FALSE,
        na = ""
    )
    utils::write.csv(
        population$cell_coverage,
        file = file.path(outputDir, "population_cell_coverage.csv"),
        row.names = FALSE,
        na = ""
    )
    utils::write.csv(
        population$missing_runs,
        file = file.path(outputDir, "population_missing_runs.csv"),
        row.names = FALSE,
        na = ""
    )
    invisible(outputDir)
}

print_acmm_population_summary <- function(population)
{
    acmm_validate_population(population)
    summary <- population$summary[1, , drop = FALSE]
    cat("Population policy:", summary$population_policy, "\n")
    cat(
        "Available valid runs:", summary$available_valid_runs,
        "of", summary$selected_planned_runs, "selected planned runs\n"
    )
    cat("Missing planned runs:", summary$missing_planned_runs, "\n")

    if (summary$missing_planned_runs > 0)
    {
        cat(
            "NOTE: Missing planned runs are treated as missing observations, not as ",
            "violations or compliant outcomes.\n",
            sep = ""
        )
    }
    invisible(population)
}

print_acmm_export_source <- function(binding)
{
    acmm_assert(
        is.list(binding),
        "print_acmm_export_source() requires a validated export binding.",
        "acmm_analysis_api_error"
    )
    if (identical(binding$source_kind, "campaign"))
    {
        cat("Campaign: ", binding$campaign_id, "\n", sep = "")
        cat("Merge: ", binding$merge_id, "\n", sep = "")
    }
    else
    {
        cat("Execution: ", binding$execution_id, "\n", sep = "")
    }
    invisible(binding)
}
