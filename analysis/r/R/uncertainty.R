acmm_validate_confidence_level <- function(confidenceLevel)
{
    acmm_assert(
        is.numeric(confidenceLevel) && length(confidenceLevel) == 1 &&
            is.finite(confidenceLevel) && confidenceLevel > 0 && confidenceLevel < 1,
        "confidenceLevel must be one finite numeric value strictly between 0 and 1.",
        "acmm_analysis_api_error"
    )
    as.numeric(confidenceLevel)
}

acmm_validate_bootstrap_replicates <- function(bootstrapReplicates)
{
    acmm_assert(
        is.numeric(bootstrapReplicates) && length(bootstrapReplicates) == 1 &&
            is.finite(bootstrapReplicates) &&
            bootstrapReplicates == trunc(bootstrapReplicates) &&
            bootstrapReplicates >= 1,
        "bootstrapReplicates must be one integer >= 1.",
        "acmm_analysis_api_error"
    )
    as.numeric(bootstrapReplicates)
}

acmm_validate_bootstrap_seed <- function(seed)
{
    acmm_assert(
        is.numeric(seed) && length(seed) == 1 && is.finite(seed) &&
            seed == trunc(seed) && seed >= 0 && seed <= .Machine$integer.max,
        paste0(
            "seed must be one integer between 0 and ",
            .Machine$integer.max,
            "."
        ),
        "acmm_analysis_api_error"
    )
    as.integer(seed)
}

acmm_with_seed <- function(seed, expression)
{
    seed <- acmm_validate_bootstrap_seed(seed)
    hadSeed <- exists(".Random.seed", envir = .GlobalEnv, inherits = FALSE)

    if (hadSeed)
    {
        previousSeed <- get(".Random.seed", envir = .GlobalEnv, inherits = FALSE)
    }

    on.exit(
        {
            if (hadSeed)
            {
                assign(".Random.seed", previousSeed, envir = .GlobalEnv)
            }
            else if (exists(".Random.seed", envir = .GlobalEnv, inherits = FALSE))
            {
                rm(".Random.seed", envir = .GlobalEnv)
            }
        },
        add = TRUE
    )

    set.seed(seed)
    force(expression)
}

acmm_wilson_bounds <- function(successes, trials, confidenceLevel)
{
    confidenceLevel <- acmm_validate_confidence_level(confidenceLevel)

    if (trials == 0)
    {
        return(c(lower = NA_real_, upper = NA_real_))
    }

    acmm_assert(
        is.numeric(successes) && length(successes) == 1 && is.finite(successes) &&
            is.numeric(trials) && length(trials) == 1 && is.finite(trials) &&
            successes >= 0 && trials >= successes,
        "Wilson interval counts must satisfy 0 <= successes <= trials.",
        "acmm_analysis_api_error"
    )

    alpha <- 1 - confidenceLevel
    zValue <- stats::qnorm(1 - alpha / 2)
    pHat <- successes / trials
    zSquared <- zValue * zValue
    denominator <- 1 + zSquared / trials
    center <- (pHat + zSquared / (2 * trials)) / denominator
    halfWidth <- zValue * sqrt(
        pHat * (1 - pHat) / trials + zSquared / (4 * trials * trials)
    ) / denominator

    c(
        lower = max(0, center - halfWidth),
        upper = min(1, center + halfWidth)
    )
}

interval_acmm_wilson <- function(
    population,
    groupBy = acmmDefaultGroupBy,
    confidenceLevel = 0.95,
    assumeIndependent = FALSE
)
{
    acmm_validate_population(population)
    confidenceLevel <- acmm_validate_confidence_level(confidenceLevel)
    acmm_assert(
        is.logical(assumeIndependent) && length(assumeIndependent) == 1 &&
            !is.na(assumeIndependent),
        "assumeIndependent must be one non-missing logical value.",
        "acmm_analysis_api_error"
    )
    acmm_assert(
        isTRUE(assumeIndependent),
        paste0(
            "Wilson intervals require an explicit independent-binomial assumption. ",
            "Set assumeIndependent = TRUE only when that assumption is justified."
        ),
        "acmm_assumption_error"
    )

    estimates <- estimate_acmm_opportunity(population, groupBy)
    if (nrow(estimates) == 0)
    {
        return(estimates)
    }

    pBounds <- t(vapply(
        seq_len(nrow(estimates)),
        function(index)
        {
            acmm_wilson_bounds(
                estimates$compliant[[index]],
                estimates$adjudicable[[index]],
                confidenceLevel
            )
        },
        numeric(2)
    ))

    coverageBounds <- t(vapply(
        seq_len(nrow(estimates)),
        function(index)
        {
            acmm_wilson_bounds(
                estimates$adjudicable[[index]],
                estimates$applicable[[index]],
                confidenceLevel
            )
        },
        numeric(2)
    ))

    estimates$confidence_level <- confidenceLevel
    estimates$interval_method <- "wilson_score_independent_binomial"
    estimates$independence_assumed <- TRUE
    estimates$p_hat_lower <- pBounds[, "lower"]
    estimates$p_hat_upper <- pBounds[, "upper"]
    estimates$coverage_lower <- coverageBounds[, "lower"]
    estimates$coverage_upper <- coverageBounds[, "upper"]
    estimates
}

acmm_validate_hierarchical_group_by <- function(judgments, groupBy)
{
    acmm_validate_group_by(judgments, groupBy)
    unitColumns <- intersect(groupBy, c("scenario_id", "run_id"))
    acmm_assert(
        length(unitColumns) == 0,
        paste0(
            "Hierarchical bootstrap grouping cannot include resampled unit column(s): ",
            paste(unitColumns, collapse = ", "),
            "."
        ),
        "acmm_analysis_api_error"
    )
}

acmm_population_from_judgments <- function(judgments)
{
    structure(
        list(
            binding = list(),
            runs = data.frame(),
            judgments = judgments,
            excluded_attempts = data.frame()
        ),
        class = "acmm_population"
    )
}

acmm_bootstrap_group_judgments <- function(judgments)
{
    scenarioIds <- unique(judgments$scenario_id)
    sampledScenarios <- sample(
        scenarioIds,
        size = length(scenarioIds),
        replace = TRUE
    )

    rows <- list()
    rowIndex <- 1

    for (scenarioIndex in seq_along(sampledScenarios))
    {
        sourceScenario <- sampledScenarios[[scenarioIndex]]
        scenarioRows <- judgments$scenario_id == sourceScenario
        runIds <- unique(judgments$run_id[scenarioRows])
        sampledRuns <- sample(runIds, size = length(runIds), replace = TRUE)

        for (runIndex in seq_along(sampledRuns))
        {
            sourceRun <- sampledRuns[[runIndex]]
            sourceRows <- scenarioRows & judgments$run_id == sourceRun
            sampled <- judgments[sourceRows, , drop = FALSE]
            sampled$scenario_id <- paste0("bootstrap_s", scenarioIndex)
            sampled$run_id <- paste0("bootstrap_s", scenarioIndex, "_r", runIndex)
            rows[[rowIndex]] <- sampled
            rowIndex <- rowIndex + 1
        }
    }

    result <- do.call(rbind, rows)
    rownames(result) <- NULL
    result
}

acmm_interval_status <- function(pointValue, clusterCount, bootstrapValues)
{
    if (is.na(pointValue))
    {
        return("point_not_estimable")
    }
    if (clusterCount < 2)
    {
        return("insufficient_scenario_clusters")
    }

    finiteCount <- sum(is.finite(bootstrapValues))
    if (finiteCount < 2)
    {
        return("insufficient_bootstrap_replicates")
    }
    "ok"
}

acmm_percentile_bounds <- function(
    pointValue,
    clusterCount,
    bootstrapValues,
    confidenceLevel
)
{
    status <- acmm_interval_status(pointValue, clusterCount, bootstrapValues)
    finiteValues <- bootstrapValues[is.finite(bootstrapValues)]

    if (status != "ok")
    {
        return(list(
            lower = NA_real_,
            upper = NA_real_,
            replicates = length(finiteValues),
            status = status
        ))
    }

    alpha <- 1 - confidenceLevel
    bounds <- stats::quantile(
        finiteValues,
        probs = c(alpha / 2, 1 - alpha / 2),
        names = FALSE,
        type = 7
    )

    list(
        lower = as.numeric(bounds[[1]]),
        upper = as.numeric(bounds[[2]]),
        replicates = length(finiteValues),
        status = status
    )
}

acmm_point_estimates_for_group <- function(judgments, groupBy)
{
    population <- acmm_population_from_judgments(judgments)
    estimate_acmm_compliance(population, groupBy)
}

acmm_mean_or_na <- function(values)
{
    finiteValues <- values[!is.na(values)]
    if (length(finiteValues) == 0)
    {
        return(NA_real_)
    }
    mean(finiteValues)
}

acmm_bootstrap_run_units <- function(judgments)
{
    acmm_run_unit_summaries(judgments, character(0))
}

acmm_bootstrap_estimates_from_run_units <- function(
    runUnits,
    sampledUnitRows,
    sampledScenarioIndexes
)
{
    sampledUnits <- runUnits[sampledUnitRows, , drop = FALSE]
    counts <- c(
        compliant = sum(sampledUnits$compliant),
        violation = sum(sampledUnits$violation),
        unadjudicable = sum(sampledUnits$unadjudicable),
        not_applicable = sum(sampledUnits$not_applicable)
    )
    opportunity <- acmm_counts_to_summary(counts)

    scenarioRows <- split(
        seq_len(nrow(sampledUnits)),
        sampledScenarioIndexes,
        drop = TRUE
    )
    scenarioPHat <- vapply(
        scenarioRows,
        function(rows) acmm_mean_or_na(sampledUnits$p_hat[rows]),
        numeric(1)
    )
    scenarioCoverage <- vapply(
        scenarioRows,
        function(rows) acmm_mean_or_na(sampledUnits$coverage[rows]),
        numeric(1)
    )

    c(
        opportunity_p_hat = opportunity[["p_hat"]],
        opportunity_coverage = opportunity[["coverage"]],
        run_p_hat = acmm_mean_or_na(sampledUnits$p_hat),
        run_coverage = acmm_mean_or_na(sampledUnits$coverage),
        scenario_p_hat = acmm_mean_or_na(scenarioPHat),
        scenario_coverage = acmm_mean_or_na(scenarioCoverage)
    )
}

acmm_bootstrap_values_for_group <- function(judgments, groupBy, bootstrapReplicates)
{
    result <- list(
        opportunity_p_hat = rep(NA_real_, bootstrapReplicates),
        opportunity_coverage = rep(NA_real_, bootstrapReplicates),
        run_p_hat = rep(NA_real_, bootstrapReplicates),
        run_coverage = rep(NA_real_, bootstrapReplicates),
        scenario_p_hat = rep(NA_real_, bootstrapReplicates),
        scenario_coverage = rep(NA_real_, bootstrapReplicates)
    )

    runUnits <- acmm_bootstrap_run_units(judgments)
    scenarioIds <- unique(judgments$scenario_id)
    runIdsByScenario <- lapply(
        scenarioIds,
        function(scenarioId)
        {
            unique(judgments$run_id[judgments$scenario_id == scenarioId])
        }
    )
    names(runIdsByScenario) <- scenarioIds
    unitRowByRunId <- seq_len(nrow(runUnits))
    names(unitRowByRunId) <- runUnits$run_id

    for (replicateIndex in seq_len(bootstrapReplicates))
    {
        sampledScenarios <- sample(
            scenarioIds,
            size = length(scenarioIds),
            replace = TRUE
        )
        sampledUnitRows <- integer(0)
        sampledScenarioIndexes <- integer(0)

        for (scenarioIndex in seq_along(sampledScenarios))
        {
            sourceScenario <- sampledScenarios[[scenarioIndex]]
            sourceRunIds <- runIdsByScenario[[sourceScenario]]
            sampledRunIds <- sample(
                sourceRunIds,
                size = length(sourceRunIds),
                replace = TRUE
            )
            sampledUnitRows <- c(
                sampledUnitRows,
                unname(unitRowByRunId[sampledRunIds])
            )
            sampledScenarioIndexes <- c(
                sampledScenarioIndexes,
                rep.int(scenarioIndex, length(sampledRunIds))
            )
        }

        estimates <- acmm_bootstrap_estimates_from_run_units(
            runUnits,
            sampledUnitRows,
            sampledScenarioIndexes
        )
        for (name in names(result))
        {
            result[[name]][[replicateIndex]] <- estimates[[name]]
        }
    }
    result
}

acmm_interval_row <- function(
    pointRow,
    estimatorName,
    scenarioClusters,
    runClusters,
    bootstrapReplicates,
    confidenceLevel,
    bootstrapValues
)
{
    pInterval <- acmm_percentile_bounds(
        pointRow$p_hat[[1]],
        scenarioClusters,
        bootstrapValues[[paste0(estimatorName, "_p_hat")]],
        confidenceLevel
    )
    coverageInterval <- acmm_percentile_bounds(
        pointRow$coverage[[1]],
        scenarioClusters,
        bootstrapValues[[paste0(estimatorName, "_coverage")]],
        confidenceLevel
    )

    pointRow$confidence_level <- confidenceLevel
    pointRow$interval_method <- "scenario_run_cluster_percentile_bootstrap"
    pointRow$scenario_clusters <- scenarioClusters
    pointRow$run_clusters <- runClusters
    pointRow$bootstrap_replicates_requested <- bootstrapReplicates
    pointRow$p_hat_lower <- pInterval$lower
    pointRow$p_hat_upper <- pInterval$upper
    pointRow$p_hat_bootstrap_replicates <- pInterval$replicates
    pointRow$p_hat_interval_status <- pInterval$status
    pointRow$coverage_lower <- coverageInterval$lower
    pointRow$coverage_upper <- coverageInterval$upper
    pointRow$coverage_bootstrap_replicates <- coverageInterval$replicates
    pointRow$coverage_interval_status <- coverageInterval$status
    pointRow
}

interval_acmm_hierarchical <- function(
    population,
    groupBy = acmmDefaultGroupBy,
    confidenceLevel = 0.95,
    bootstrapReplicates = 2000,
    seed
)
{
    acmm_validate_population(population)
    judgments <- population$judgments
    acmm_validate_hierarchical_group_by(judgments, groupBy)
    confidenceLevel <- acmm_validate_confidence_level(confidenceLevel)
    bootstrapReplicates <- acmm_validate_bootstrap_replicates(bootstrapReplicates)
    seed <- acmm_validate_bootstrap_seed(seed)

    groups <- acmm_group_rows(judgments, groupBy)
    if (length(groups) == 0)
    {
        return(list(
            opportunity = data.frame(),
            run = data.frame(),
            scenario = data.frame()
        ))
    }

    compute <- function()
    {
        opportunityRows <- list()
        runRows <- list()
        scenarioRows <- list()

        for (groupIndex in seq_along(groups))
        {
            groupRows <- groups[[groupIndex]]
            groupJudgments <- judgments[groupRows, , drop = FALSE]
            scenarioClusters <- length(unique(groupJudgments$scenario_id))
            runClusters <- length(unique(groupJudgments$run_id))
            points <- acmm_point_estimates_for_group(groupJudgments, groupBy)

            if (scenarioClusters >= 2)
            {
                bootstrapValues <- acmm_bootstrap_values_for_group(
                    groupJudgments,
                    groupBy,
                    bootstrapReplicates
                )
            }
            else
            {
                bootstrapValues <- list(
                    opportunity_p_hat = numeric(0),
                    opportunity_coverage = numeric(0),
                    run_p_hat = numeric(0),
                    run_coverage = numeric(0),
                    scenario_p_hat = numeric(0),
                    scenario_coverage = numeric(0)
                )
            }

            opportunityRows[[groupIndex]] <- acmm_interval_row(
                points$opportunity,
                "opportunity",
                scenarioClusters,
                runClusters,
                bootstrapReplicates,
                confidenceLevel,
                bootstrapValues
            )
            runRows[[groupIndex]] <- acmm_interval_row(
                points$run,
                "run",
                scenarioClusters,
                runClusters,
                bootstrapReplicates,
                confidenceLevel,
                bootstrapValues
            )
            scenarioRows[[groupIndex]] <- acmm_interval_row(
                points$scenario,
                "scenario",
                scenarioClusters,
                runClusters,
                bootstrapReplicates,
                confidenceLevel,
                bootstrapValues
            )
        }

        list(
            opportunity = do.call(rbind, opportunityRows),
            run = do.call(rbind, runRows),
            scenario = do.call(rbind, scenarioRows)
        )
    }

    result <- acmm_with_seed(seed, compute())
    for (tableName in names(result))
    {
        rownames(result[[tableName]]) <- NULL
    }
    result
}

acmm_dimension_bootstrap_values_for_group <- function(
    judgments,
    methods,
    requirementGroupBy,
    dimensionGroupBy,
    bootstrapReplicates
)
{
    estimatorNames <- c("opportunity", "run", "scenario")
    result <- list()

    for (estimatorName in estimatorNames)
    {
        for (method in methods)
        {
            key <- paste(estimatorName, method, sep = "_")
            result[[key]] <- rep(NA_real_, bootstrapReplicates)
        }
    }

    requirementGroups <- acmm_group_rows(judgments, requirementGroupBy)
    requirementContexts <- lapply(requirementGroups, function(groupRows)
    {
        requirementJudgments <- judgments[groupRows, , drop = FALSE]
        runUnits <- acmm_bootstrap_run_units(requirementJudgments)
        unitRowByRunId <- seq_len(nrow(runUnits))
        names(unitRowByRunId) <- runUnits$run_id

        list(
            run_units = runUnits,
            unit_row_by_run_id = unitRowByRunId
        )
    })

    scenarioIds <- unique(judgments$scenario_id)
    runIdsByScenario <- lapply(
        scenarioIds,
        function(scenarioId)
        {
            unique(judgments$run_id[judgments$scenario_id == scenarioId])
        }
    )
    names(runIdsByScenario) <- scenarioIds

    for (replicateIndex in seq_len(bootstrapReplicates))
    {
        sampledScenarios <- sample(
            scenarioIds,
            size = length(scenarioIds),
            replace = TRUE
        )
        sampledRunIds <- character(0)
        sampledScenarioIndexes <- integer(0)

        for (scenarioIndex in seq_along(sampledScenarios))
        {
            sourceScenario <- sampledScenarios[[scenarioIndex]]
            sourceRunIds <- runIdsByScenario[[sourceScenario]]
            sampledRuns <- sample(
                sourceRunIds,
                size = length(sourceRunIds),
                replace = TRUE
            )
            sampledRunIds <- c(sampledRunIds, sampledRuns)
            sampledScenarioIndexes <- c(
                sampledScenarioIndexes,
                rep.int(scenarioIndex, length(sampledRuns))
            )
        }

        requirementEstimates <- list(
            opportunity = rep(NA_real_, length(requirementContexts)),
            run = rep(NA_real_, length(requirementContexts)),
            scenario = rep(NA_real_, length(requirementContexts))
        )

        for (requirementIndex in seq_along(requirementContexts))
        {
            context <- requirementContexts[[requirementIndex]]
            sampledUnitRows <- unname(
                context$unit_row_by_run_id[sampledRunIds]
            )
            presentRows <- !is.na(sampledUnitRows)

            if (any(presentRows))
            {
                estimates <- acmm_bootstrap_estimates_from_run_units(
                    context$run_units,
                    sampledUnitRows[presentRows],
                    sampledScenarioIndexes[presentRows]
                )
                requirementEstimates$opportunity[[requirementIndex]] <-
                    estimates[["opportunity_p_hat"]]
                requirementEstimates$run[[requirementIndex]] <-
                    estimates[["run_p_hat"]]
                requirementEstimates$scenario[[requirementIndex]] <-
                    estimates[["scenario_p_hat"]]
            }
        }

        for (estimatorName in estimatorNames)
        {
            scores <- requirementEstimates[[estimatorName]]
            for (method in methods)
            {
                key <- paste(estimatorName, method, sep = "_")
                if (!anyNA(scores))
                {
                    result[[key]][[replicateIndex]] <-
                        acmm_dimension_score(scores, method)
                }
            }
        }
    }

    result
}

acmm_dimension_interval_row <- function(
    pointRow,
    estimatorName,
    scenarioClusters,
    runClusters,
    bootstrapReplicates,
    confidenceLevel,
    bootstrapValues
)
{
    method <- pointRow$aggregation_method[[1]]
    key <- paste(estimatorName, method, sep = "_")
    interval <- acmm_percentile_bounds(
        pointRow$s_d[[1]],
        scenarioClusters,
        bootstrapValues[[key]],
        confidenceLevel
    )

    pointRow$confidence_level <- confidenceLevel
    pointRow$interval_method <- "scenario_run_cluster_percentile_bootstrap"
    pointRow$scenario_clusters <- scenarioClusters
    pointRow$run_clusters <- runClusters
    pointRow$bootstrap_replicates_requested <- bootstrapReplicates
    pointRow$s_d_lower <- interval$lower
    pointRow$s_d_upper <- interval$upper
    pointRow$s_d_bootstrap_replicates <- interval$replicates
    pointRow$s_d_interval_status <- interval$status
    pointRow
}

interval_acmm_dimension_scores_hierarchical <- function(
    population,
    methods = acmmDimensionMethods,
    requirementGroupBy = acmmDefaultGroupBy,
    dimensionGroupBy = acmmDefaultDimensionGroupBy,
    confidenceLevel = 0.95,
    bootstrapReplicates = 2000,
    seed
)
{
    acmm_validate_population(population)
    judgments <- population$judgments
    methods <- acmm_validate_dimension_methods(methods)
    acmm_validate_hierarchical_group_by(judgments, requirementGroupBy)
    acmm_validate_hierarchical_group_by(judgments, dimensionGroupBy)
    confidenceLevel <- acmm_validate_confidence_level(confidenceLevel)
    bootstrapReplicates <- acmm_validate_bootstrap_replicates(bootstrapReplicates)
    seed <- acmm_validate_bootstrap_seed(seed)

    pointScores <- estimate_acmm_dimension_scores(
        population,
        methods = methods,
        requirementGroupBy = requirementGroupBy,
        dimensionGroupBy = dimensionGroupBy
    )
    groups <- acmm_group_rows(judgments, dimensionGroupBy)

    if (length(groups) == 0)
    {
        return(list(
            opportunity = data.frame(),
            run = data.frame(),
            scenario = data.frame()
        ))
    }

    compute <- function()
    {
        rowsByEstimator <- list(
            opportunity = list(),
            run = list(),
            scenario = list()
        )

        for (groupIndex in seq_along(groups))
        {
            groupRows <- groups[[groupIndex]]
            groupJudgments <- judgments[groupRows, , drop = FALSE]
            groupValues <- acmm_group_values(judgments, groupRows, dimensionGroupBy)
            scenarioClusters <- length(unique(groupJudgments$scenario_id))
            runClusters <- length(unique(groupJudgments$run_id))

            if (scenarioClusters >= 2)
            {
                bootstrapValues <- acmm_dimension_bootstrap_values_for_group(
                    groupJudgments,
                    methods,
                    requirementGroupBy,
                    dimensionGroupBy,
                    bootstrapReplicates
                )
            }
            else
            {
                bootstrapValues <- list()
                for (estimatorName in names(rowsByEstimator))
                {
                    for (method in methods)
                    {
                        key <- paste(estimatorName, method, sep = "_")
                        bootstrapValues[[key]] <- numeric(0)
                    }
                }
            }

            for (estimatorName in names(rowsByEstimator))
            {
                estimatorPoints <- pointScores[[estimatorName]]
                pointRows <- rep(TRUE, nrow(estimatorPoints))
                for (columnName in dimensionGroupBy)
                {
                    pointRows <- pointRows &
                        estimatorPoints[[columnName]] == groupValues[[columnName]]
                }

                groupPoints <- estimatorPoints[pointRows, , drop = FALSE]
                acmm_assert(
                    nrow(groupPoints) == length(methods),
                    "Dimension point estimates are inconsistent with bootstrap groups.",
                    "acmm_analysis_error"
                )

                for (method in methods)
                {
                    methodRow <- groupPoints[
                        groupPoints$aggregation_method == method,
                        ,
                        drop = FALSE
                    ]
                    rowIndex <- length(rowsByEstimator[[estimatorName]]) + 1
                    rowsByEstimator[[estimatorName]][[rowIndex]] <-
                        acmm_dimension_interval_row(
                            methodRow,
                            estimatorName,
                            scenarioClusters,
                            runClusters,
                            bootstrapReplicates,
                            confidenceLevel,
                            bootstrapValues
                        )
                }
            }
        }

        lapply(rowsByEstimator, function(rows)
        {
            result <- do.call(rbind, rows)
            rownames(result) <- NULL
            result
        })
    }

    acmm_with_seed(seed, compute())
}
