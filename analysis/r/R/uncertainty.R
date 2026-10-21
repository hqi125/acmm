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

    for (replicateIndex in seq_len(bootstrapReplicates))
    {
        sampledJudgments <- acmm_bootstrap_group_judgments(judgments)
        estimates <- acmm_point_estimates_for_group(sampledJudgments, groupBy)

        result$opportunity_p_hat[[replicateIndex]] <- estimates$opportunity$p_hat[[1]]
        result$opportunity_coverage[[replicateIndex]] <- estimates$opportunity$coverage[[1]]
        result$run_p_hat[[replicateIndex]] <- estimates$run$p_hat[[1]]
        result$run_coverage[[replicateIndex]] <- estimates$run$coverage[[1]]
        result$scenario_p_hat[[replicateIndex]] <- estimates$scenario$p_hat[[1]]
        result$scenario_coverage[[replicateIndex]] <- estimates$scenario$coverage[[1]]
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
