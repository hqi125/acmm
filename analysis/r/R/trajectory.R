analyze_acmm_trajectory <- function(exportData, populationPolicy = NULL)
{
    acmm_assert(
        inherits(exportData, "acmm_export"),
        "analyze_acmm_trajectory() requires a validated acmm_export object.",
        "acmm_analysis_api_error"
    )
    acmm_assert(
        identical(exportData$binding$source_kind, "campaign") &&
            identical(exportData$binding$schema_version, acmmCampaignExportSchemaVersion),
        paste0(
            "Trajectory analysis requires Campaign export schema ",
            acmmCampaignExportSchemaVersion,
            " with normalized tool_calls.csv and tool_results.csv tables."
        ),
        "acmm_analysis_api_error"
    )
    acmm_assert(
        !is.null(exportData$tool_calls) && !is.null(exportData$tool_results),
        "Trajectory analysis requires normalized tool-call and tool-result tables.",
        "acmm_analysis_api_error"
    )

    population <- build_acmm_population(exportData, populationPolicy = populationPolicy)
    selectedRuns <- population$runs
    selectedRunIds <- selectedRuns$run_id
    calls <- exportData$tool_calls[
        exportData$tool_calls$run_id %in% selectedRunIds,
        ,
        drop = FALSE
    ]
    results <- exportData$tool_results[
        exportData$tool_results$run_id %in% selectedRunIds,
        ,
        drop = FALSE
    ]

    runMetrics <- acmm_trajectory_run_metrics(selectedRuns, calls, results)
    subjectSummary <- acmm_trajectory_subject_summary(runMetrics, calls, results)
    toolUsage <- acmm_trajectory_tool_usage(selectedRuns, calls, results)

    structure(
        list(
            population = population,
            run_metrics = runMetrics,
            subject_summary = subjectSummary,
            tool_usage = toolUsage
        ),
        class = "acmm_trajectory_analysis"
    )
}

acmm_trajectory_run_metrics <- function(selectedRuns, calls, results)
{
    rows <- vector("list", nrow(selectedRuns))
    for (index in seq_len(nrow(selectedRuns)))
    {
        run <- selectedRuns[index, , drop = FALSE]
        runId <- run$run_id[[1]]
        runCalls <- calls[calls$run_id == runId, , drop = FALSE]
        runResults <- results[results$run_id == runId, , drop = FALSE]
        callNames <- runCalls$tool_name
        callIds <- runCalls$call_id
        resultIds <- runResults$tool_call_id
        matchedResultIds <- resultIds[nzchar(resultIds)]
        matchedCallIds <- callIds[nzchar(callIds)]
        unmatchedCalls <- sum(!nzchar(callIds) | !(callIds %in% matchedResultIds))
        unmatchedResults <- sum(!nzchar(resultIds) | !(resultIds %in% matchedCallIds))
        repeatedCalls <- length(callNames) - length(unique(callNames))
        consecutiveRepeatedCalls <- 0L
        if (length(callNames) >= 2)
        {
            consecutiveRepeatedCalls <- sum(
                callNames[-1] == callNames[-length(callNames)]
            )
        }
        errorResults <- if (nrow(runResults) == 0) 0L else sum(runResults$is_error)
        errorRate <- if (nrow(runResults) == 0) NA_real_ else errorResults / nrow(runResults)

        rows[[index]] <- data.frame(
            run_index = run$run_index[[1]],
            logical_run_id = run$logical_run_id[[1]],
            run_id = runId,
            subject_id = run$subject_id[[1]],
            scenario_id = run$scenario_id[[1]],
            repetition = run$repetition[[1]],
            tool_calls = nrow(runCalls),
            distinct_tools = length(unique(callNames)),
            tool_results = nrow(runResults),
            error_results = errorResults,
            error_rate = errorRate,
            repeated_calls = repeatedCalls,
            consecutive_repeated_calls = consecutiveRepeatedCalls,
            unmatched_calls = unmatchedCalls,
            unmatched_results = unmatchedResults,
            stringsAsFactors = FALSE
        )
    }
    if (length(rows) == 0)
    {
        return(data.frame())
    }
    result <- do.call(rbind, rows)
    rownames(result) <- NULL
    result[order(result$subject_id, result$run_index), , drop = FALSE]
}

acmm_trajectory_subject_summary <- function(runMetrics, calls, results)
{
    subjects <- sort(unique(runMetrics$subject_id))
    rows <- lapply(subjects, function(subjectId)
    {
        runRows <- runMetrics[runMetrics$subject_id == subjectId, , drop = FALSE]
        callRows <- calls[calls$subject_id == subjectId, , drop = FALSE]
        resultRows <- results[results$subject_id == subjectId, , drop = FALSE]
        errorResults <- if (nrow(resultRows) == 0) 0L else sum(resultRows$is_error)
        data.frame(
            subject_id = subjectId,
            selected_runs = nrow(runRows),
            total_tool_calls = sum(runRows$tool_calls),
            mean_tool_calls_per_run = mean(runRows$tool_calls),
            median_tool_calls_per_run = stats::median(runRows$tool_calls),
            total_distinct_tool_names = length(unique(callRows$tool_name)),
            mean_distinct_tools_per_run = mean(runRows$distinct_tools),
            total_tool_results = sum(runRows$tool_results),
            error_results = errorResults,
            error_rate = if (nrow(resultRows) == 0) NA_real_ else errorResults / nrow(resultRows),
            repeated_calls = sum(runRows$repeated_calls),
            consecutive_repeated_calls = sum(runRows$consecutive_repeated_calls),
            unmatched_calls = sum(runRows$unmatched_calls),
            unmatched_results = sum(runRows$unmatched_results),
            stringsAsFactors = FALSE
        )
    })
    if (length(rows) == 0)
    {
        return(data.frame())
    }
    result <- do.call(rbind, rows)
    rownames(result) <- NULL
    result
}

acmm_trajectory_tool_usage <- function(selectedRuns, calls, results)
{
    subjects <- sort(unique(selectedRuns$subject_id))
    rows <- list()
    rowIndex <- 1L
    for (subjectId in subjects)
    {
        subjectRuns <- selectedRuns[selectedRuns$subject_id == subjectId, , drop = FALSE]
        subjectCalls <- calls[calls$subject_id == subjectId, , drop = FALSE]
        subjectResults <- results[results$subject_id == subjectId, , drop = FALSE]
        tools <- sort(unique(c(subjectCalls$tool_name, subjectResults$tool_name)))
        for (toolName in tools)
        {
            callRows <- subjectCalls[subjectCalls$tool_name == toolName, , drop = FALSE]
            resultRows <- subjectResults[subjectResults$tool_name == toolName, , drop = FALSE]
            errorResults <- if (nrow(resultRows) == 0) 0L else sum(resultRows$is_error)
            runsUsingTool <- length(unique(callRows$run_id))
            rows[[rowIndex]] <- data.frame(
                subject_id = subjectId,
                tool_name = toolName,
                calls = nrow(callRows),
                runs_using_tool = runsUsingTool,
                run_share = if (nrow(subjectRuns) == 0) NA_real_ else runsUsingTool / nrow(subjectRuns),
                results = nrow(resultRows),
                error_results = errorResults,
                error_rate = if (nrow(resultRows) == 0) NA_real_ else errorResults / nrow(resultRows),
                stringsAsFactors = FALSE
            )
            rowIndex <- rowIndex + 1L
        }
    }
    if (length(rows) == 0)
    {
        return(data.frame(
            subject_id = character(0),
            tool_name = character(0),
            calls = integer(0),
            runs_using_tool = integer(0),
            run_share = numeric(0),
            results = integer(0),
            error_results = integer(0),
            error_rate = numeric(0),
            stringsAsFactors = FALSE
        ))
    }
    result <- do.call(rbind, rows)
    rownames(result) <- NULL
    result[order(result$subject_id, -result$calls, result$tool_name), , drop = FALSE]
}
