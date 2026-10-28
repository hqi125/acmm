acmm_interaction_attach_factors <- function(population, factorSpec)
{
    acmm_validate_population(population)
    acmm_assert(
        is.list(factorSpec) && length(factorSpec) == 2 && !is.null(names(factorSpec)),
        "factorSpec must define exactly two named factors.",
        "acmm_analysis_api_error"
    )

    runs <- population$runs
    factorNames <- names(factorSpec)

    for (factorName in factorNames)
    {
        spec <- factorSpec[[factorName]]
        acmm_assert(
            is.list(spec) && is.character(spec$json_key) && length(spec$json_key) == 1 &&
                is.character(spec$low_label) && length(spec$low_label) == 1 &&
                is.character(spec$high_label) && length(spec$high_label) == 1,
            paste0("Invalid factor specification for '", factorName, "'."),
            "acmm_analysis_api_error"
        )

        values <- vapply(
            seq_len(nrow(runs)),
            function(index)
            {
                configuration <- acmm_parse_json(
                    runs$environment_configuration_json[[index]],
                    paste0("environment_configuration_json run row ", index)
                )
                rawValue <- configuration[[spec$json_key]]
                acmm_assert(
                    !is.null(rawValue),
                    paste0(
                        "Environment configuration is missing interaction factor key '",
                        spec$json_key,
                        "'."
                    ),
                    "acmm_interaction_design_error"
                )

                if (identical(rawValue, spec$low_value))
                {
                    return(spec$low_label)
                }
                if (identical(rawValue, spec$high_value))
                {
                    return(spec$high_label)
                }

                acmm_abort(
                    paste0(
                        "Unexpected value for interaction factor '", factorName,
                        "' in run ", runs$run_id[[index]], "."
                    ),
                    "acmm_interaction_design_error"
                )
            },
            character(1)
        )
        runs[[factorName]] <- values
    }

    judgments <- population$judgments
    runRows <- match(judgments$run_id, runs$run_id)
    acmm_assert(
        all(!is.na(runRows)),
        "Interaction judgments could not be matched to selected valid runs.",
        "acmm_integrity_error"
    )

    for (factorName in factorNames)
    {
        judgments[[factorName]] <- runs[[factorName]][runRows]
    }

    structure(
        list(
            population = population,
            runs = runs,
            judgments = judgments,
            factor_spec = factorSpec
        ),
        class = "acmm_interaction_data"
    )
}

acmm_interaction_run_units <- function(interactionData)
{
    acmm_assert(
        inherits(interactionData, "acmm_interaction_data"),
        "Interaction run-unit construction requires acmm_interaction_data.",
        "acmm_analysis_api_error"
    )

    judgments <- interactionData$judgments
    factorNames <- names(interactionData$factor_spec)
    groupBy <- c(
        "subject_id",
        "requirement_id",
        "dimension",
        factorNames,
        "repetition"
    )
    acmm_run_unit_summaries(judgments, groupBy)
}

acmm_interaction_cell_estimates <- function(interactionData)
{
    runUnits <- acmm_interaction_run_units(interactionData)
    if (nrow(runUnits) == 0)
    {
        return(data.frame())
    }

    factorNames <- names(interactionData$factor_spec)
    groupBy <- c("subject_id", "requirement_id", "dimension", factorNames)
    groups <- acmm_group_rows(runUnits, groupBy)
    rows <- lapply(groups, function(groupRows)
    {
        groupValues <- acmm_group_values(runUnits, groupRows, groupBy)
        pValues <- runUnits$p_hat[groupRows]
        coverageValues <- runUnits$coverage[groupRows]
        c(
            groupValues,
            list(
                total_runs = length(groupRows),
                estimable_runs = sum(!is.na(pValues)),
                applicable_runs = sum(!is.na(coverageValues)),
                p_hat = acmm_mean_or_na(pValues),
                coverage = acmm_mean_or_na(coverageValues)
            )
        )
    })

    result <- acmm_bind_rows(rows)
    result[order(result$subject_id, result$dimension, result$requirement_id), , drop = FALSE]
}

acmm_interaction_cell_value <- function(
    cellEstimates,
    subjectId,
    requirementId,
    factorAName,
    factorALevel,
    factorBName,
    factorBLevel
)
{
    rows <- cellEstimates$subject_id == subjectId &
        cellEstimates$requirement_id == requirementId &
        cellEstimates[[factorAName]] == factorALevel &
        cellEstimates[[factorBName]] == factorBLevel

    if (sum(rows) != 1)
    {
        return(NA_real_)
    }
    cellEstimates$p_hat[rows][[1]]
}

acmm_interaction_simple_effects <- function(interactionData, cellEstimates = NULL)
{
    if (is.null(cellEstimates))
    {
        cellEstimates <- acmm_interaction_cell_estimates(interactionData)
    }
    if (nrow(cellEstimates) == 0)
    {
        return(data.frame())
    }

    factorNames <- names(interactionData$factor_spec)
    factorAName <- factorNames[[1]]
    factorBName <- factorNames[[2]]
    factorA <- interactionData$factor_spec[[factorAName]]
    factorB <- interactionData$factor_spec[[factorBName]]

    keys <- unique(cellEstimates[c("subject_id", "requirement_id", "dimension")])
    rows <- list()
    rowIndex <- 1

    addEffect <- function(
        subjectId,
        requirementId,
        dimension,
        effectFactor,
        stratumFactor,
        stratumLevel,
        lowValue,
        highValue
    )
    {
        estimable <- is.finite(lowValue) && is.finite(highValue)
        rows[[rowIndex]] <<- data.frame(
            subject_id = subjectId,
            requirement_id = requirementId,
            dimension = dimension,
            effect_factor = effectFactor,
            stratum_factor = stratumFactor,
            stratum_level = stratumLevel,
            low_rate = lowValue,
            high_rate = highValue,
            risk_difference = if (estimable) highValue - lowValue else NA_real_,
            status = if (estimable) "ok" else "not_estimable",
            stringsAsFactors = FALSE
        )
        rowIndex <<- rowIndex + 1
    }

    for (index in seq_len(nrow(keys)))
    {
        subjectId <- keys$subject_id[[index]]
        requirementId <- keys$requirement_id[[index]]
        dimension <- keys$dimension[[index]]

        p00 <- acmm_interaction_cell_value(
            cellEstimates,
            subjectId,
            requirementId,
            factorAName,
            factorA$low_label,
            factorBName,
            factorB$low_label
        )
        p01 <- acmm_interaction_cell_value(
            cellEstimates,
            subjectId,
            requirementId,
            factorAName,
            factorA$low_label,
            factorBName,
            factorB$high_label
        )
        p10 <- acmm_interaction_cell_value(
            cellEstimates,
            subjectId,
            requirementId,
            factorAName,
            factorA$high_label,
            factorBName,
            factorB$low_label
        )
        p11 <- acmm_interaction_cell_value(
            cellEstimates,
            subjectId,
            requirementId,
            factorAName,
            factorA$high_label,
            factorBName,
            factorB$high_label
        )

        addEffect(
            subjectId,
            requirementId,
            dimension,
            factorBName,
            factorAName,
            factorA$low_label,
            p00,
            p01
        )
        addEffect(
            subjectId,
            requirementId,
            dimension,
            factorBName,
            factorAName,
            factorA$high_label,
            p10,
            p11
        )
        addEffect(
            subjectId,
            requirementId,
            dimension,
            factorAName,
            factorBName,
            factorB$low_label,
            p00,
            p10
        )
        addEffect(
            subjectId,
            requirementId,
            dimension,
            factorAName,
            factorBName,
            factorB$high_label,
            p01,
            p11
        )
    }

    result <- do.call(rbind, rows)
    rownames(result) <- NULL
    result
}

acmm_interaction_point_row <- function(interactionData, cellEstimates, keyRow)
{
    factorNames <- names(interactionData$factor_spec)
    factorAName <- factorNames[[1]]
    factorBName <- factorNames[[2]]
    factorA <- interactionData$factor_spec[[factorAName]]
    factorB <- interactionData$factor_spec[[factorBName]]
    subjectId <- keyRow$subject_id[[1]]
    requirementId <- keyRow$requirement_id[[1]]
    dimension <- keyRow$dimension[[1]]

    p00 <- acmm_interaction_cell_value(
        cellEstimates,
        subjectId,
        requirementId,
        factorAName,
        factorA$low_label,
        factorBName,
        factorB$low_label
    )
    p01 <- acmm_interaction_cell_value(
        cellEstimates,
        subjectId,
        requirementId,
        factorAName,
        factorA$low_label,
        factorBName,
        factorB$high_label
    )
    p10 <- acmm_interaction_cell_value(
        cellEstimates,
        subjectId,
        requirementId,
        factorAName,
        factorA$high_label,
        factorBName,
        factorB$low_label
    )
    p11 <- acmm_interaction_cell_value(
        cellEstimates,
        subjectId,
        requirementId,
        factorAName,
        factorA$high_label,
        factorBName,
        factorB$high_label
    )
    estimable <- all(is.finite(c(p00, p01, p10, p11)))

    data.frame(
        subject_id = subjectId,
        requirement_id = requirementId,
        dimension = dimension,
        factor_a = factorAName,
        factor_a_low = factorA$low_label,
        factor_a_high = factorA$high_label,
        factor_b = factorBName,
        factor_b_low = factorB$low_label,
        factor_b_high = factorB$high_label,
        p_low_low = p00,
        p_low_high = p01,
        p_high_low = p10,
        p_high_high = p11,
        effect_factor_b_at_factor_a_low = if (estimable) p01 - p00 else NA_real_,
        effect_factor_b_at_factor_a_high = if (estimable) p11 - p10 else NA_real_,
        interaction_risk_difference = if (estimable) {
            (p11 - p10) - (p01 - p00)
        } else {
            NA_real_
        },
        interaction_status = if (estimable) "estimable" else {
            "not_estimable_incomplete_factorial"
        },
        stringsAsFactors = FALSE
    )
}

acmm_interaction_bootstrap_values <- function(runUnits, interactionData, bootstrapReplicates)
{
    factorNames <- names(interactionData$factor_spec)
    factorAName <- factorNames[[1]]
    factorBName <- factorNames[[2]]
    factorA <- interactionData$factor_spec[[factorAName]]
    factorB <- interactionData$factor_spec[[factorBName]]
    repetitions <- sort(unique(runUnits$repetition))
    values <- rep(NA_real_, bootstrapReplicates)

    for (replicateIndex in seq_len(bootstrapReplicates))
    {
        sampledRepetitions <- sample(
            repetitions,
            size = length(repetitions),
            replace = TRUE
        )
        sampledRows <- integer(0)
        for (sampledRepetition in sampledRepetitions)
        {
            sampledRows <- c(
                sampledRows,
                which(runUnits$repetition == sampledRepetition)
            )
        }
        sampled <- runUnits[sampledRows, , drop = FALSE]

        cellMean <- function(factorALevel, factorBLevel)
        {
            rows <- sampled[[factorAName]] == factorALevel &
                sampled[[factorBName]] == factorBLevel
            acmm_mean_or_na(sampled$p_hat[rows])
        }

        p00 <- cellMean(factorA$low_label, factorB$low_label)
        p01 <- cellMean(factorA$low_label, factorB$high_label)
        p10 <- cellMean(factorA$high_label, factorB$low_label)
        p11 <- cellMean(factorA$high_label, factorB$high_label)

        if (all(is.finite(c(p00, p01, p10, p11))))
        {
            values[[replicateIndex]] <- (p11 - p10) - (p01 - p00)
        }
    }
    values
}

interval_acmm_interaction_block_bootstrap <- function(
    interactionData,
    bootstrapReplicates = 2000,
    seed = 12345,
    confidenceLevel = 0.95
)
{
    bootstrapReplicates <- acmm_validate_bootstrap_replicates(bootstrapReplicates)
    seed <- acmm_validate_bootstrap_seed(seed)
    confidenceLevel <- acmm_validate_confidence_level(confidenceLevel)
    cellEstimates <- acmm_interaction_cell_estimates(interactionData)
    runUnits <- acmm_interaction_run_units(interactionData)

    if (nrow(cellEstimates) == 0)
    {
        return(data.frame())
    }

    keys <- unique(cellEstimates[c("subject_id", "requirement_id", "dimension")])
    rows <- vector("list", nrow(keys))

    acmm_with_seed(seed, {
        for (index in seq_len(nrow(keys)))
        {
            keyRow <- keys[index, , drop = FALSE]
            point <- acmm_interaction_point_row(interactionData, cellEstimates, keyRow)
            groupRows <- runUnits$subject_id == keyRow$subject_id[[1]] &
                runUnits$requirement_id == keyRow$requirement_id[[1]] &
                runUnits$dimension == keyRow$dimension[[1]]
            groupUnits <- runUnits[groupRows, , drop = FALSE]
            repetitions <- unique(groupUnits$repetition)
            finitePHat <- groupUnits$p_hat[is.finite(groupUnits$p_hat)]
            degenerate <- length(finitePHat) > 0 && length(unique(finitePHat)) == 1

            bootstrapValues <- rep(NA_real_, bootstrapReplicates)
            intervalStatus <- point$interaction_status[[1]]
            lower <- NA_real_
            upper <- NA_real_
            finiteReplicates <- 0

            if (intervalStatus == "estimable")
            {
                if (length(repetitions) < 2)
                {
                    intervalStatus <- "insufficient_repetition_blocks"
                }
                else if (degenerate)
                {
                    intervalStatus <- "degenerate_observed_outcome"
                }
                else
                {
                    bootstrapValues <- acmm_interaction_bootstrap_values(
                        groupUnits,
                        interactionData,
                        bootstrapReplicates
                    )
                    finiteValues <- bootstrapValues[is.finite(bootstrapValues)]
                    finiteReplicates <- length(finiteValues)
                    if (finiteReplicates < 2)
                    {
                        intervalStatus <- "insufficient_bootstrap_replicates"
                    }
                    else
                    {
                        alpha <- 1 - confidenceLevel
                        bounds <- stats::quantile(
                            finiteValues,
                            probs = c(alpha / 2, 1 - alpha / 2),
                            names = FALSE,
                            type = 7
                        )
                        lower <- as.numeric(bounds[[1]])
                        upper <- as.numeric(bounds[[2]])
                        intervalStatus <- "ok"
                    }
                }
            }

            point$confidence_level <- confidenceLevel
            point$interval_method <- "repetition_block_percentile_bootstrap"
            point$repetition_blocks <- length(repetitions)
            point$bootstrap_replicates_requested <- bootstrapReplicates
            point$bootstrap_replicates_finite <- finiteReplicates
            point$interaction_lower <- lower
            point$interaction_upper <- upper
            point$interval_status <- intervalStatus
            rows[[index]] <- point
        }
    })

    result <- do.call(rbind, rows)
    rownames(result) <- NULL
    result
}

write_acmm_interaction_csv <- function(table, outputPath)
{
    outputDir <- dirname(outputPath)
    if (!dir.exists(outputDir))
    {
        dir.create(outputDir, recursive = TRUE, showWarnings = FALSE)
    }
    utils::write.csv(table, file = outputPath, row.names = FALSE, na = "")
    invisible(outputPath)
}

acmm_interaction_plot_cells <- function(
    cellEstimates,
    interactionData,
    dimensionName,
    outputPath,
    title
)
{
    rows <- cellEstimates$dimension == dimensionName
    data <- cellEstimates[rows, , drop = FALSE]
    if (nrow(data) == 0)
    {
        return(FALSE)
    }

    factorNames <- names(interactionData$factor_spec)
    factorAName <- factorNames[[1]]
    factorBName <- factorNames[[2]]
    factorA <- interactionData$factor_spec[[factorAName]]
    factorB <- interactionData$factor_spec[[factorBName]]
    subjects <- unique(data$subject_id)

    dir.create(dirname(outputPath), recursive = TRUE, showWarnings = FALSE)
    grDevices::png(outputPath, width = 1600, height = 520, res = 140)
    oldPar <- graphics::par(no.readonly = TRUE)
    on.exit({
        graphics::par(oldPar)
        grDevices::dev.off()
    }, add = TRUE)

    graphics::par(
        mfrow = c(1, length(subjects)),
        mar = c(5, 4, 4, 1) + 0.1,
        oma = c(0, 0, 3, 0)
    )

    for (subjectId in subjects)
    {
        subjectRows <- data$subject_id == subjectId
        subjectData <- data[subjectRows, , drop = FALSE]
        graphics::plot(
            c(1, 2),
            c(0, 1),
            type = "n",
            xaxt = "n",
            xlab = "Ordinary write outcome",
            ylab = "Observed compliance rate",
            main = subjectId,
            ylim = c(0, 1)
        )
        graphics::axis(1, at = c(1, 2), labels = c(factorA$low_label, factorA$high_label))
        graphics::abline(h = c(0, 0.5, 1), lty = c(1, 3, 1))

        for (factorBIndex in seq_along(c(factorB$low_label, factorB$high_label)))
        {
            factorBLevel <- c(factorB$low_label, factorB$high_label)[[factorBIndex]]
            values <- c(
                acmm_interaction_cell_value(
                    subjectData,
                    subjectId,
                    subjectData$requirement_id[[1]],
                    factorAName,
                    factorA$low_label,
                    factorBName,
                    factorBLevel
                ),
                acmm_interaction_cell_value(
                    subjectData,
                    subjectId,
                    subjectData$requirement_id[[1]],
                    factorAName,
                    factorA$high_label,
                    factorBName,
                    factorBLevel
                )
            )
            graphics::lines(
                c(1, 2),
                values,
                type = "b",
                pch = if (factorBIndex == 1) 16 else 17,
                lty = factorBIndex
            )
        }
        graphics::legend(
            "bottomleft",
            legend = c(factorB$low_label, factorB$high_label),
            pch = c(16, 17),
            lty = c(1, 2),
            title = "Authorized recovery",
            bty = "n"
        )
    }
    graphics::mtext(title, side = 3, outer = TRUE, line = -1.3, font = 2)
    TRUE
}

acmm_interaction_plot_contrasts <- function(interactionTable, outputPath)
{
    estimable <- interactionTable[is.finite(interactionTable$interaction_risk_difference), , drop = FALSE]
    if (nrow(estimable) == 0)
    {
        return(FALSE)
    }

    dir.create(dirname(outputPath), recursive = TRUE, showWarnings = FALSE)
    grDevices::png(outputPath, width = 1500, height = 850, res = 140)
    on.exit(grDevices::dev.off(), add = TRUE)

    labels <- paste(estimable$subject_id, estimable$dimension, sep = " | ")
    y <- seq_len(nrow(estimable))
    graphics::plot(
        estimable$interaction_risk_difference,
        y,
        xlim = c(-1, 1),
        ylim = c(0.5, nrow(estimable) + 0.5),
        yaxt = "n",
        xlab = "Interaction risk difference",
        ylab = "",
        pch = 16,
        main = "Recovery x ordinary-write interaction contrasts"
    )
    graphics::axis(2, at = y, labels = labels, las = 2, cex.axis = 0.75)
    graphics::abline(v = 0, lty = 2)

    intervalRows <- is.finite(estimable$interaction_lower) &
        is.finite(estimable$interaction_upper)
    if (any(intervalRows))
    {
        graphics::arrows(
            estimable$interaction_lower[intervalRows],
            y[intervalRows],
            estimable$interaction_upper[intervalRows],
            y[intervalRows],
            angle = 90,
            code = 3,
            length = 0.05
        )
    }
    TRUE
}
