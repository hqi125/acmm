acmm_population_missing_runs <- function(runs)
{
    slotIndices <- sort(unique(runs$run_index))
    rows <- list()
    rowIndex <- 1

    for (runIndex in slotIndices)
    {
        slotRows <- which(runs$run_index == runIndex)
        selectedRows <- slotRows[runs$selected[slotRows] & runs$validity[slotRows] == "valid"]
        if (length(selectedRows) == 1)
        {
            next
        }

        finalRow <- tail(slotRows, 1)
        rows[[rowIndex]] <- data.frame(
            run_index = runs$run_index[finalRow],
            subject_id = runs$subject_id[finalRow],
            scenario_id = runs$scenario_id[finalRow],
            repetition = runs$repetition[finalRow],
            attempts = length(slotRows),
            final_termination = runs$termination[finalRow],
            final_validity = runs$validity[finalRow],
            failure_kind = runs$failure_kind[finalRow],
            failure_message = runs$failure_message[finalRow],
            stringsAsFactors = FALSE
        )
        rowIndex <- rowIndex + 1
    }

    if (length(rows) == 0)
    {
        return(data.frame(
            run_index = numeric(0),
            subject_id = character(0),
            scenario_id = character(0),
            repetition = numeric(0),
            attempts = integer(0),
            final_termination = character(0),
            final_validity = character(0),
            failure_kind = character(0),
            failure_message = character(0),
            stringsAsFactors = FALSE
        ))
    }

    result <- do.call(rbind, rows)
    rownames(result) <- NULL
    result
}

acmm_population_cell_coverage <- function(runs)
{
    slotIndices <- sort(unique(runs$run_index))
    slotRows <- vapply(
        slotIndices,
        function(runIndex) tail(which(runs$run_index == runIndex), 1),
        integer(1)
    )
    slots <- runs[slotRows, , drop = FALSE]
    slots$available_valid <- vapply(
        slotIndices,
        function(runIndex)
        {
            rows <- which(runs$run_index == runIndex)
            sum(runs$selected[rows] & runs$validity[rows] == "valid") == 1
        },
        logical(1)
    )

    keys <- paste(slots$subject_id, slots$scenario_id, sep = "\037")
    groups <- split(seq_len(nrow(slots)), keys, drop = TRUE)
    rows <- lapply(groups, function(groupRows)
    {
        data.frame(
            subject_id = slots$subject_id[groupRows[[1]]],
            scenario_id = slots$scenario_id[groupRows[[1]]],
            planned_runs = length(groupRows),
            available_valid_runs = sum(slots$available_valid[groupRows]),
            missing_runs = sum(!slots$available_valid[groupRows]),
            availability_fraction = mean(slots$available_valid[groupRows]),
            stringsAsFactors = FALSE
        )
    })

    if (length(rows) == 0)
    {
        return(data.frame())
    }

    result <- do.call(rbind, rows)
    rownames(result) <- NULL
    result[order(result$subject_id, result$scenario_id), , drop = FALSE]
}

build_acmm_population <- function(exportData, populationPolicy = NULL)
{
    acmm_assert(
        inherits(exportData, "acmm_export"),
        "build_acmm_population() requires a validated acmm_export object.",
        "acmm_analysis_api_error"
    )

    if (is.null(populationPolicy))
    {
        populationPolicy <- exportData$population_policy
    }
    populationPolicy <- acmm_validate_population_policy(populationPolicy)

    if (populationPolicy == "strict")
    {
        acmm_assert(
            isTRUE(exportData$complete_selection),
            "Strict population policy requires every selected planned run to resolve.",
            "acmm_incomplete_execution_error"
        )
    }

    selectedMask <- exportData$runs$selected & exportData$runs$validity == "valid"
    selectedRuns <- exportData$runs[selectedMask, , drop = FALSE]
    excludedAttempts <- exportData$runs[!selectedMask, , drop = FALSE]

    selectedRunIds <- selectedRuns$run_id
    judgmentMask <- exportData$judgments$run_id %in% selectedRunIds
    selectedJudgments <- exportData$judgments[judgmentMask, , drop = FALSE]

    if (nrow(selectedJudgments) > 0)
    {
        acmm_assert(
            all(selectedJudgments$selected & selectedJudgments$validity == "valid"),
            "Statistical population contains a non-selected or invalid judgment.",
            "acmm_integrity_error"
        )
    }

    expectedJudgments <- sum(selectedRuns$judgment_count)
    acmm_assert(
        nrow(selectedJudgments) == expectedJudgments,
        "Selected judgment population does not match selected Run Record counts.",
        "acmm_integrity_error"
    )

    selectedPlannedRuns <- unique(exportData$runs$execution_selected_run_count)
    missingRuns <- acmm_population_missing_runs(exportData$runs)
    cellCoverage <- acmm_population_cell_coverage(exportData$runs)
    availableValidRuns <- nrow(selectedRuns)

    summary <- data.frame(
        population_policy = populationPolicy,
        complete_selection = isTRUE(exportData$complete_selection),
        selected_planned_runs = selectedPlannedRuns,
        available_valid_runs = availableValidRuns,
        missing_planned_runs = nrow(missingRuns),
        availability_fraction = if (selectedPlannedRuns == 0) {
            NA_real_
        } else {
            availableValidRuns / selectedPlannedRuns
        },
        excluded_attempt_rows = nrow(excludedAttempts),
        stringsAsFactors = FALSE
    )

    structure(
        list(
            binding = exportData$binding,
            policy = populationPolicy,
            complete_selection = exportData$complete_selection,
            runs = selectedRuns,
            judgments = selectedJudgments,
            excluded_attempts = excludedAttempts,
            missing_runs = missingRuns,
            cell_coverage = cellCoverage,
            summary = summary
        ),
        class = "acmm_population"
    )
}
