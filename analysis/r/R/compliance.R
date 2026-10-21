acmm_validate_population <- function(population)
{
    acmm_assert(
        inherits(population, "acmm_population"),
        "Compliance estimation requires an acmm_population object.",
        "acmm_analysis_api_error"
    )
    acmm_assert(
        is.data.frame(population$judgments),
        "acmm_population judgments must be a data frame.",
        "acmm_analysis_api_error"
    )
}

acmm_validate_group_by <- function(judgments, groupBy)
{
    acmm_assert(
        is.character(groupBy) && length(groupBy) > 0 && !anyDuplicated(groupBy),
        "groupBy must be a non-empty character vector without duplicates.",
        "acmm_analysis_api_error"
    )
    missingColumns <- setdiff(groupBy, names(judgments))
    acmm_assert(
        length(missingColumns) == 0,
        paste0("Unknown grouping column(s): ", paste(missingColumns, collapse = ", "), "."),
        "acmm_analysis_api_error"
    )
}

acmm_group_rows <- function(data, groupBy)
{
    if (nrow(data) == 0)
    {
        return(list())
    }

    keyParts <- lapply(groupBy, function(columnName) data[[columnName]])
    keys <- do.call(paste, c(keyParts, sep = "\037"))
    split(seq_len(nrow(data)), keys, drop = TRUE)
}

acmm_group_values <- function(data, rows, groupBy)
{
    result <- lapply(groupBy, function(columnName) data[[columnName]][rows[[1]]])
    names(result) <- groupBy
    result
}

acmm_outcome_counts <- function(outcomes)
{
    c(
        compliant = sum(outcomes == "compliant"),
        violation = sum(outcomes == "violation"),
        unadjudicable = sum(outcomes == "unadjudicable"),
        not_applicable = sum(outcomes == "not_applicable")
    )
}

acmm_ratio <- function(numerator, denominator)
{
    if (denominator == 0)
    {
        return(NA_real_)
    }
    numerator / denominator
}

acmm_counts_to_summary <- function(counts)
{
    adjudicable <- counts[["compliant"]] + counts[["violation"]]
    applicable <- adjudicable + counts[["unadjudicable"]]

    c(
        compliant = counts[["compliant"]],
        violation = counts[["violation"]],
        unadjudicable = counts[["unadjudicable"]],
        not_applicable = counts[["not_applicable"]],
        adjudicable = adjudicable,
        applicable = applicable,
        p_hat = acmm_ratio(counts[["compliant"]], adjudicable),
        coverage = acmm_ratio(adjudicable, applicable)
    )
}

acmm_bind_rows <- function(rows)
{
    if (length(rows) == 0)
    {
        return(data.frame())
    }
    result <- do.call(rbind, lapply(rows, as.data.frame, stringsAsFactors = FALSE))
    rownames(result) <- NULL
    result
}

estimate_acmm_opportunity <- function(population, groupBy = acmmDefaultGroupBy)
{
    acmm_validate_population(population)
    judgments <- population$judgments
    acmm_validate_group_by(judgments, groupBy)

    groups <- acmm_group_rows(judgments, groupBy)
    rows <- lapply(groups, function(groupRows)
    {
        groupValues <- acmm_group_values(judgments, groupRows, groupBy)
        summary <- acmm_counts_to_summary(
            acmm_outcome_counts(judgments$outcome[groupRows])
        )
        c(groupValues, as.list(summary))
    })
    acmm_bind_rows(rows)
}

acmm_run_unit_summaries <- function(judgments, groupBy)
{
    unitGroupBy <- c(groupBy, "run_id", "scenario_id")
    unitGroupBy <- unique(unitGroupBy)
    groups <- acmm_group_rows(judgments, unitGroupBy)

    rows <- lapply(groups, function(groupRows)
    {
        groupValues <- acmm_group_values(judgments, groupRows, unitGroupBy)
        summary <- acmm_counts_to_summary(
            acmm_outcome_counts(judgments$outcome[groupRows])
        )
        c(groupValues, as.list(summary))
    })
    acmm_bind_rows(rows)
}

estimate_acmm_run <- function(population, groupBy = acmmDefaultGroupBy)
{
    acmm_validate_population(population)
    judgments <- population$judgments
    acmm_validate_group_by(judgments, groupBy)

    units <- acmm_run_unit_summaries(judgments, groupBy)
    if (nrow(units) == 0)
    {
        return(data.frame())
    }

    groups <- acmm_group_rows(units, groupBy)
    rows <- lapply(groups, function(groupRows)
    {
        groupValues <- acmm_group_values(units, groupRows, groupBy)
        pValues <- units$p_hat[groupRows]
        coverageValues <- units$coverage[groupRows]

        c(
            groupValues,
            list(
                total_runs = length(groupRows),
                estimable_runs = sum(!is.na(pValues)),
                applicable_runs = sum(!is.na(coverageValues)),
                p_hat = if (all(is.na(pValues))) NA_real_ else mean(pValues, na.rm = TRUE),
                coverage = if (all(is.na(coverageValues))) {
                    NA_real_
                } else {
                    mean(coverageValues, na.rm = TRUE)
                }
            )
        )
    })
    acmm_bind_rows(rows)
}

estimate_acmm_scenario <- function(population, groupBy = acmmDefaultGroupBy)
{
    acmm_validate_population(population)
    judgments <- population$judgments
    acmm_validate_group_by(judgments, groupBy)

    runUnits <- acmm_run_unit_summaries(judgments, groupBy)
    if (nrow(runUnits) == 0)
    {
        return(data.frame())
    }

    scenarioGroupBy <- unique(c(groupBy, "scenario_id"))
    scenarioGroups <- acmm_group_rows(runUnits, scenarioGroupBy)
    scenarioRows <- lapply(scenarioGroups, function(groupRows)
    {
        groupValues <- acmm_group_values(runUnits, groupRows, scenarioGroupBy)
        pValues <- runUnits$p_hat[groupRows]
        coverageValues <- runUnits$coverage[groupRows]

        c(
            groupValues,
            list(
                p_hat = if (all(is.na(pValues))) NA_real_ else mean(pValues, na.rm = TRUE),
                coverage = if (all(is.na(coverageValues))) {
                    NA_real_
                } else {
                    mean(coverageValues, na.rm = TRUE)
                }
            )
        )
    })
    scenarioUnits <- acmm_bind_rows(scenarioRows)

    groups <- acmm_group_rows(scenarioUnits, groupBy)
    rows <- lapply(groups, function(groupRows)
    {
        groupValues <- acmm_group_values(scenarioUnits, groupRows, groupBy)
        pValues <- scenarioUnits$p_hat[groupRows]
        coverageValues <- scenarioUnits$coverage[groupRows]

        c(
            groupValues,
            list(
                total_scenarios = length(groupRows),
                estimable_scenarios = sum(!is.na(pValues)),
                applicable_scenarios = sum(!is.na(coverageValues)),
                p_hat = if (all(is.na(pValues))) NA_real_ else mean(pValues, na.rm = TRUE),
                coverage = if (all(is.na(coverageValues))) {
                    NA_real_
                } else {
                    mean(coverageValues, na.rm = TRUE)
                }
            )
        )
    })
    acmm_bind_rows(rows)
}

estimate_acmm_compliance <- function(population, groupBy = acmmDefaultGroupBy)
{
    list(
        opportunity = estimate_acmm_opportunity(population, groupBy),
        run = estimate_acmm_run(population, groupBy),
        scenario = estimate_acmm_scenario(population, groupBy)
    )
}
