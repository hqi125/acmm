acmm_availability_slot_table <- function(runs, planned = NULL)
{
    acmm_assert(
        is.data.frame(runs),
        "Availability analysis requires a validated runs table.",
        "acmm_analysis_api_error"
    )

    if (!is.null(planned))
    {
        acmm_assert(
            is.data.frame(planned) && nrow(planned) > 0,
            "Campaign availability analysis requires a non-empty planned_runs table.",
            "acmm_analysis_api_error"
        )
        rows <- lapply(seq_len(nrow(planned)), function(plannedRow)
        {
            runIndex <- planned$run_index[[plannedRow]]
            slotRows <- which(runs$run_index == runIndex)
            if (length(slotRows) == 0)
            {
                return(data.frame(
                    run_index = runIndex,
                    subject_id = planned$subject_id[[plannedRow]],
                    scenario_id = planned$scenario_id[[plannedRow]],
                    repetition = planned$repetition[[plannedRow]],
                    selected_position_within_repetition = NA_integer_,
                    attempts_used = 0,
                    max_attempts_per_run = NA_real_,
                    first_attempt_valid = FALSE,
                    retry_attempted = FALSE,
                    recovered_by_retry = FALSE,
                    available_valid = FALSE,
                    valid_attempt = NA_real_,
                    final_termination = "",
                    final_validity = "",
                    failure_kind = "not_attempted",
                    failure_message = "No persisted Campaign attempt.",
                    stringsAsFactors = FALSE
                ))
            }

            slotRows <- slotRows[order(runs$attempt[slotRows])]
            firstRow <- slotRows[[1]]
            finalRow <- tail(slotRows, 1)
            selectedRows <- slotRows[
                runs$selected[slotRows] & runs$validity[slotRows] == "valid"
            ]
            available <- length(selectedRows) == 1
            validAttempt <- if (available) runs$attempt[selectedRows[[1]]] else NA_real_
            maxAttempts <- if ("max_attempts_per_run" %in% names(runs)) {
                runs$max_attempts_per_run[finalRow]
            } else {
                NA_real_
            }
            data.frame(
                run_index = runIndex,
                subject_id = planned$subject_id[[plannedRow]],
                scenario_id = planned$scenario_id[[plannedRow]],
                repetition = planned$repetition[[plannedRow]],
                selected_position_within_repetition = NA_integer_,
                attempts_used = length(slotRows),
                max_attempts_per_run = maxAttempts,
                first_attempt_valid = runs$validity[firstRow] == "valid",
                retry_attempted = length(slotRows) > 1,
                recovered_by_retry = available && !is.na(validAttempt) && validAttempt > 1,
                available_valid = available,
                valid_attempt = validAttempt,
                final_termination = runs$termination[finalRow],
                final_validity = runs$validity[finalRow],
                failure_kind = if (available) "" else runs$failure_kind[finalRow],
                failure_message = if (available) "" else runs$failure_message[finalRow],
                stringsAsFactors = FALSE
            )
        })
        result <- do.call(rbind, rows)
    }
    else
    {
        acmm_assert(
            nrow(runs) > 0,
            "Availability analysis requires a non-empty validated runs table.",
            "acmm_analysis_api_error"
        )
        slotIndices <- sort(unique(runs$run_index))
        rows <- lapply(slotIndices, function(runIndex)
        {
            slotRows <- which(runs$run_index == runIndex)
            slotRows <- slotRows[order(runs$attempt[slotRows])]
            firstRow <- slotRows[[1]]
            finalRow <- tail(slotRows, 1)
            selectedRows <- slotRows[
                runs$selected[slotRows] & runs$validity[slotRows] == "valid"
            ]
            available <- length(selectedRows) == 1
            validAttempt <- if (available) runs$attempt[selectedRows[[1]]] else NA_real_

            data.frame(
                run_index = runs$run_index[finalRow],
                subject_id = runs$subject_id[finalRow],
                scenario_id = runs$scenario_id[finalRow],
                repetition = runs$repetition[finalRow],
                selected_position_within_repetition = NA_integer_,
                attempts_used = length(slotRows),
                max_attempts_per_run = runs$max_attempts_per_run[finalRow],
                first_attempt_valid = runs$validity[firstRow] == "valid",
                retry_attempted = length(slotRows) > 1,
                recovered_by_retry = available && !is.na(validAttempt) && validAttempt > 1,
                available_valid = available,
                valid_attempt = validAttempt,
                final_termination = runs$termination[finalRow],
                final_validity = runs$validity[finalRow],
                failure_kind = if (available) "" else runs$failure_kind[finalRow],
                failure_message = if (available) "" else runs$failure_message[finalRow],
                stringsAsFactors = FALSE
            )
        })
        result <- do.call(rbind, rows)
    }

    rownames(result) <- NULL
    blockKeys <- paste(result$subject_id, result$repetition, sep = "\037")
    blocks <- split(seq_len(nrow(result)), blockKeys, drop = TRUE)
    for (blockRows in blocks)
    {
        ordered <- blockRows[order(result$run_index[blockRows])]
        result$selected_position_within_repetition[ordered] <- seq_along(ordered)
    }

    result[order(result$run_index), , drop = FALSE]
}

acmm_availability_summary_row <- function(slots, runs, scope, subjectId = "")
{
    planned <- nrow(slots)
    available <- sum(slots$available_valid)
    retried <- sum(slots$retry_attempted)
    recovered <- sum(slots$recovered_by_retry)

    data.frame(
        scope = scope,
        subject_id = subjectId,
        planned_slots = planned,
        available_valid_slots = available,
        unavailable_slots = planned - available,
        availability_fraction = acmm_ratio(available, planned),
        first_attempt_valid_slots = sum(slots$first_attempt_valid),
        first_attempt_availability_fraction = acmm_ratio(
            sum(slots$first_attempt_valid),
            planned
        ),
        retried_slots = retried,
        recovered_by_retry_slots = recovered,
        retry_recovery_fraction = acmm_ratio(recovered, retried),
        total_attempts = nrow(runs),
        invalid_attempts = sum(runs$validity == "invalid"),
        mean_attempts_per_slot = acmm_ratio(nrow(runs), planned),
        stringsAsFactors = FALSE
    )
}

acmm_availability_summary <- function(slots, runs)
{
    rows <- list(acmm_availability_summary_row(slots, runs, "overall"))
    rowIndex <- 2

    for (subjectId in sort(unique(slots$subject_id)))
    {
        subjectSlots <- slots[slots$subject_id == subjectId, , drop = FALSE]
        subjectRuns <- runs[runs$subject_id == subjectId, , drop = FALSE]
        rows[[rowIndex]] <- acmm_availability_summary_row(
            subjectSlots,
            subjectRuns,
            "subject",
            subjectId
        )
        rowIndex <- rowIndex + 1
    }

    result <- do.call(rbind, rows)
    rownames(result) <- NULL
    result
}

acmm_availability_group_summary <- function(slots, groupBy)
{
    groups <- acmm_group_rows(slots, groupBy)
    rows <- lapply(groups, function(groupRows)
    {
        groupValues <- acmm_group_values(slots, groupRows, groupBy)
        planned <- length(groupRows)
        available <- sum(slots$available_valid[groupRows])
        firstAttempt <- sum(slots$first_attempt_valid[groupRows])
        retried <- sum(slots$retry_attempted[groupRows])
        recovered <- sum(slots$recovered_by_retry[groupRows])

        c(
            groupValues,
            list(
                planned_slots = planned,
                available_valid_slots = available,
                unavailable_slots = planned - available,
                availability_fraction = acmm_ratio(available, planned),
                first_attempt_valid_slots = firstAttempt,
                first_attempt_availability_fraction = acmm_ratio(firstAttempt, planned),
                retried_slots = retried,
                recovered_by_retry_slots = recovered
            )
        )
    })

    result <- acmm_bind_rows(rows)
    if (nrow(result) == 0)
    {
        return(result)
    }
    rownames(result) <- NULL
    result
}

acmm_availability_failure_modes <- function(slots)
{
    missing <- slots[!slots$available_valid, , drop = FALSE]
    if (nrow(missing) == 0)
    {
        return(data.frame(
            scope = character(0),
            subject_id = character(0),
            final_termination = character(0),
            failure_kind = character(0),
            unavailable_slots = integer(0),
            fraction_of_scope_unavailable = numeric(0),
            fraction_of_scope_planned = numeric(0),
            stringsAsFactors = FALSE
        ))
    }

    buildRows <- function(scopeSlots, scopeMissing, scope, subjectId)
    {
        keys <- paste(
            scopeMissing$final_termination,
            scopeMissing$failure_kind,
            sep = "\037"
        )
        groups <- split(seq_len(nrow(scopeMissing)), keys, drop = TRUE)
        lapply(groups, function(groupRows)
        {
            count <- length(groupRows)
            data.frame(
                scope = scope,
                subject_id = subjectId,
                final_termination = scopeMissing$final_termination[groupRows[[1]]],
                failure_kind = scopeMissing$failure_kind[groupRows[[1]]],
                unavailable_slots = count,
                fraction_of_scope_unavailable = acmm_ratio(count, nrow(scopeMissing)),
                fraction_of_scope_planned = acmm_ratio(count, nrow(scopeSlots)),
                stringsAsFactors = FALSE
            )
        })
    }

    rows <- buildRows(slots, missing, "overall", "")
    for (subjectId in sort(unique(missing$subject_id)))
    {
        subjectSlots <- slots[slots$subject_id == subjectId, , drop = FALSE]
        subjectMissing <- missing[missing$subject_id == subjectId, , drop = FALSE]
        rows <- c(
            rows,
            buildRows(subjectSlots, subjectMissing, "subject", subjectId)
        )
    }

    result <- do.call(rbind, rows)
    rownames(result) <- NULL
    result
}

acmm_availability_attempt_outcomes <- function(runs)
{
    groupBy <- c(
        "subject_id",
        "scenario_id",
        "attempt",
        "validity",
        "termination",
        "failure_kind"
    )
    groups <- acmm_group_rows(runs, groupBy)
    rows <- lapply(groups, function(groupRows)
    {
        groupValues <- acmm_group_values(runs, groupRows, groupBy)
        c(groupValues, list(attempt_rows = length(groupRows)))
    })
    result <- acmm_bind_rows(rows)
    if (nrow(result) == 0)
    {
        return(result)
    }
    result[order(result$subject_id, result$scenario_id, result$attempt), , drop = FALSE]
}

acmm_availability_bootstrap_subject <- function(subjectSlots)
{
    scenarioIds <- unique(subjectSlots$scenario_id)
    sampledScenarios <- sample(
        scenarioIds,
        size = length(scenarioIds),
        replace = TRUE
    )
    sampledValues <- numeric(0)

    for (scenarioId in sampledScenarios)
    {
        scenarioRows <- which(subjectSlots$scenario_id == scenarioId)
        sampledRows <- sample(
            scenarioRows,
            size = length(scenarioRows),
            replace = TRUE
        )
        sampledValues <- c(
            sampledValues,
            as.numeric(subjectSlots$available_valid[sampledRows])
        )
    }

    if (length(sampledValues) == 0)
    {
        return(NA_real_)
    }
    mean(sampledValues)
}

interval_acmm_availability_hierarchical <- function(
    slots,
    confidenceLevel = 0.95,
    bootstrapReplicates = 2000,
    seed = 12345
)
{
    confidenceLevel <- acmm_validate_confidence_level(confidenceLevel)
    bootstrapReplicates <- acmm_validate_bootstrap_replicates(bootstrapReplicates)
    seed <- acmm_validate_bootstrap_seed(seed)

    subjectIds <- sort(unique(slots$subject_id))
    rows <- acmm_with_seed(seed, lapply(subjectIds, function(subjectId)
    {
        subjectSlots <- slots[slots$subject_id == subjectId, , drop = FALSE]
        scenarioCount <- length(unique(subjectSlots$scenario_id))
        point <- mean(subjectSlots$available_valid)
        degenerate <- length(unique(subjectSlots$available_valid)) == 1
        if (degenerate && scenarioCount >= 2)
        {
            bounds <- list(
                lower = NA_real_,
                upper = NA_real_,
                replicates = 0,
                status = "degenerate_observed_availability"
            )
        }
        else
        {
            values <- vapply(
                seq_len(bootstrapReplicates),
                function(index)
                {
                    acmm_availability_bootstrap_subject(subjectSlots)
                },
                numeric(1)
            )
            bounds <- acmm_percentile_bounds(
                point,
                scenarioCount,
                values,
                confidenceLevel
            )
        }

        data.frame(
            subject_id = subjectId,
            planned_slots = nrow(subjectSlots),
            scenario_clusters = scenarioCount,
            availability_fraction = point,
            confidence_level = confidenceLevel,
            interval_method = "scenario_slot_cluster_percentile_bootstrap",
            bootstrap_replicates_requested = bootstrapReplicates,
            bootstrap_replicates_finite = bounds$replicates,
            availability_lower = bounds$lower,
            availability_upper = bounds$upper,
            interval_status = bounds$status,
            stringsAsFactors = FALSE
        )
    }))

    result <- do.call(rbind, rows)
    rownames(result) <- NULL
    result
}

acmm_availability_opportunity_templates <- function(judgments)
{
    if (nrow(judgments) == 0)
    {
        return(data.frame())
    }

    uniqueOpportunities <- unique(judgments[
        c("run_id", "scenario_id", "requirement_id", "dimension", "opportunity_id")
    ])
    runGroups <- acmm_group_rows(
        uniqueOpportunities,
        c("run_id", "scenario_id", "requirement_id", "dimension")
    )
    runRows <- lapply(runGroups, function(groupRows)
    {
        groupValues <- acmm_group_values(
            uniqueOpportunities,
            groupRows,
            c("run_id", "scenario_id", "requirement_id", "dimension")
        )
        c(groupValues, list(planned_opportunities = length(groupRows)))
    })
    runCounts <- acmm_bind_rows(runRows)

    templateGroups <- acmm_group_rows(
        runCounts,
        c("scenario_id", "requirement_id", "dimension")
    )
    rows <- lapply(templateGroups, function(groupRows)
    {
        groupValues <- acmm_group_values(
            runCounts,
            groupRows,
            c("scenario_id", "requirement_id", "dimension")
        )
        counts <- unique(as.numeric(runCounts$planned_opportunities[groupRows]))
        consistent <- length(counts) == 1
        c(
            groupValues,
            list(
                planned_opportunities = if (consistent) counts[[1]] else NA_real_,
                template_status = if (consistent) {
                    "observed_consistent"
                } else {
                    "inconsistent_observed_template"
                },
                observed_template_runs = length(groupRows)
            )
        )
    })
    result <- acmm_bind_rows(rows)
    rownames(result) <- NULL
    result
}

acmm_availability_sensitivity <- function(slots, judgments)
{
    templates <- acmm_availability_opportunity_templates(judgments)
    if (nrow(templates) == 0)
    {
        return(data.frame(
            estimand = character(0),
            subject_id = character(0),
            requirement_id = character(0),
            dimension = character(0),
            observed_compliant = numeric(0),
            observed_violations = numeric(0),
            observed_unadjudicable = numeric(0),
            observed_not_applicable = numeric(0),
            observed_adjudicable = numeric(0),
            observed_p_hat = numeric(0),
            missing_planned_opportunities = numeric(0),
            all_missing_violation_lower = numeric(0),
            all_missing_compliant_upper = numeric(0),
            sensitivity_width = numeric(0),
            sensitivity_method = character(0),
            sensitivity_status = character(0),
            stringsAsFactors = FALSE
        ))
    }

    requirementKeys <- unique(templates[c("requirement_id", "dimension")])
    subjectIds <- sort(unique(slots$subject_id))
    missingSlots <- slots[!slots$available_valid, , drop = FALSE]
    rows <- list()
    rowIndex <- 1

    for (subjectId in subjectIds)
    {
        subjectJudgments <- judgments[judgments$subject_id == subjectId, , drop = FALSE]
        subjectMissing <- missingSlots[missingSlots$subject_id == subjectId, , drop = FALSE]

        for (requirementIndex in seq_len(nrow(requirementKeys)))
        {
            requirementId <- requirementKeys$requirement_id[[requirementIndex]]
            dimension <- requirementKeys$dimension[[requirementIndex]]
            observed <- subjectJudgments[
                subjectJudgments$requirement_id == requirementId &
                    subjectJudgments$dimension == dimension,
                ,
                drop = FALSE
            ]
            counts <- acmm_outcome_counts(observed$outcome)
            compliant <- counts[["compliant"]]
            violation <- counts[["violation"]]
            adjudicable <- compliant + violation

            missingOpportunities <- 0
            templateStatus <- "ok"
            if (nrow(subjectMissing) > 0)
            {
                for (missingIndex in seq_len(nrow(subjectMissing)))
                {
                    scenarioId <- subjectMissing$scenario_id[[missingIndex]]
                    matching <- templates[
                        templates$scenario_id == scenarioId &
                            templates$requirement_id == requirementId &
                            templates$dimension == dimension,
                        ,
                        drop = FALSE
                    ]
                    if (nrow(matching) == 0)
                    {
                        scenarioObserved <- any(templates$scenario_id == scenarioId)
                        if (scenarioObserved)
                        {
                            next
                        }
                        templateStatus <- "missing_or_inconsistent_opportunity_template"
                        missingOpportunities <- NA_real_
                        break
                    }
                    if (
                        nrow(matching) != 1 ||
                        matching$template_status[[1]] != "observed_consistent" ||
                        is.na(matching$planned_opportunities[[1]])
                    )
                    {
                        templateStatus <- "missing_or_inconsistent_opportunity_template"
                        missingOpportunities <- NA_real_
                        break
                    }
                    missingOpportunities <- missingOpportunities +
                        as.numeric(matching$planned_opportunities[[1]])
                }
            }

            if (is.na(missingOpportunities))
            {
                lower <- NA_real_
                upper <- NA_real_
                methodStatus <- templateStatus
            }
            else
            {
                potential <- adjudicable + missingOpportunities
                lower <- acmm_ratio(compliant, potential)
                upper <- acmm_ratio(compliant + missingOpportunities, potential)
                methodStatus <- if (potential == 0) {
                    "no_adjudicable_or_missing_opportunities"
                } else {
                    "ok"
                }
            }

            rows[[rowIndex]] <- data.frame(
                estimand = "opportunity",
                subject_id = subjectId,
                requirement_id = requirementId,
                dimension = dimension,
                observed_compliant = compliant,
                observed_violations = violation,
                observed_unadjudicable = counts[["unadjudicable"]],
                observed_not_applicable = counts[["not_applicable"]],
                observed_adjudicable = adjudicable,
                observed_p_hat = acmm_ratio(compliant, adjudicable),
                missing_planned_opportunities = missingOpportunities,
                all_missing_violation_lower = lower,
                all_missing_compliant_upper = upper,
                sensitivity_width = if (
                    is.na(lower) || is.na(upper)
                ) {
                    NA_real_
                } else {
                    upper - lower
                },
                sensitivity_method = paste0(
                    "all_missing_violations_to_all_missing_compliant_",
                    "planned_opportunity_envelope"
                ),
                sensitivity_status = methodStatus,
                stringsAsFactors = FALSE
            )
            rowIndex <- rowIndex + 1
        }
    }

    result <- do.call(rbind, rows)
    rownames(result) <- NULL
    result
}

analyze_acmm_availability <- function(
    exportData,
    confidenceLevel = 0.95,
    bootstrapReplicates = 2000,
    seed = 12345
)
{
    acmm_assert(
        inherits(exportData, "acmm_export"),
        "Availability analysis requires a validated acmm_export object.",
        "acmm_analysis_api_error"
    )

    slots <- acmm_availability_slot_table(
        exportData$runs,
        if (!is.null(exportData$planned_runs)) exportData$planned_runs else NULL
    )
    list(
        summary = acmm_availability_summary(slots, exportData$runs),
        cells = acmm_availability_group_summary(
            slots,
            c("subject_id", "scenario_id")
        ),
        positions = acmm_availability_group_summary(
            slots,
            c("subject_id", "selected_position_within_repetition")
        ),
        slots = slots,
        failure_modes = acmm_availability_failure_modes(slots),
        attempt_outcomes = acmm_availability_attempt_outcomes(exportData$runs),
        intervals = interval_acmm_availability_hierarchical(
            slots,
            confidenceLevel = confidenceLevel,
            bootstrapReplicates = bootstrapReplicates,
            seed = seed
        ),
        sensitivity = acmm_availability_sensitivity(slots, exportData$judgments)
    )
}
