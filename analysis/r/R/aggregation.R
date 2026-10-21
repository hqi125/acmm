acmm_validate_dimension_method <- function(method)
{
    acmm_assert(
        is.character(method) && length(method) == 1 &&
            !is.na(method) && method %in% acmmDimensionMethods,
        paste0(
            "method must be one of: ",
            paste(acmmDimensionMethods, collapse = ", "),
            "."
        ),
        "acmm_analysis_api_error"
    )
    method
}

acmm_validate_dimension_methods <- function(methods)
{
    acmm_assert(
        is.character(methods) && length(methods) > 0 &&
            !anyNA(methods) && !anyDuplicated(methods),
        "methods must be a non-empty character vector without NA or duplicates.",
        "acmm_analysis_api_error"
    )

    unknownMethods <- setdiff(methods, acmmDimensionMethods)
    acmm_assert(
        length(unknownMethods) == 0,
        paste0(
            "Unknown dimension aggregation method(s): ",
            paste(unknownMethods, collapse = ", "),
            "."
        ),
        "acmm_analysis_api_error"
    )
    methods
}

acmm_validate_dimension_group_by <- function(requirementEstimates, groupBy)
{
    acmm_assert(
        is.character(groupBy) && length(groupBy) > 0 &&
            !anyNA(groupBy) && !anyDuplicated(groupBy),
        "groupBy must be a non-empty character vector without NA or duplicates.",
        "acmm_analysis_api_error"
    )
    acmm_assert(
        "dimension" %in% groupBy,
        "Dimension aggregation groupBy must include dimension.",
        "acmm_analysis_api_error"
    )
    acmm_assert(
        !("requirement_id" %in% groupBy),
        "Dimension aggregation groupBy must not include requirement_id.",
        "acmm_analysis_api_error"
    )

    requiredColumns <- unique(c(groupBy, "requirement_id", "p_hat"))
    missingColumns <- setdiff(requiredColumns, names(requirementEstimates))
    acmm_assert(
        length(missingColumns) == 0,
        paste0(
            "Requirement estimate table is missing column(s): ",
            paste(missingColumns, collapse = ", "),
            "."
        ),
        "acmm_analysis_api_error"
    )
}

acmm_validate_requirement_estimates <- function(
    requirementEstimates,
    groupBy = acmmDefaultDimensionGroupBy
)
{
    acmm_assert(
        is.data.frame(requirementEstimates),
        "Dimension aggregation requires a requirement estimate data frame.",
        "acmm_analysis_api_error"
    )
    acmm_validate_dimension_group_by(requirementEstimates, groupBy)

    requirementIds <- requirementEstimates$requirement_id
    acmm_assert(
        is.character(requirementIds) && !anyNA(requirementIds) &&
            all(nzchar(requirementIds)),
        "requirement_id values must be non-empty strings.",
        "acmm_analysis_api_error"
    )

    scores <- requirementEstimates$p_hat
    acmm_assert(
        is.numeric(scores),
        "p_hat must be numeric for dimension aggregation.",
        "acmm_analysis_api_error"
    )
    acmm_assert(
        !any(is.nan(scores)),
        "p_hat must not contain NaN values.",
        "acmm_analysis_api_error"
    )

    finiteScores <- scores[!is.na(scores)]
    acmm_assert(
        all(is.finite(finiteScores)) &&
            all(finiteScores >= 0 & finiteScores <= 1),
        "Finite p_hat values must lie in the closed interval [0, 1].",
        "acmm_analysis_api_error"
    )

    identityColumns <- c(groupBy, "requirement_id")
    groups <- acmm_group_rows(requirementEstimates, identityColumns)
    duplicateGroups <- vapply(groups, length, integer(1)) != 1
    acmm_assert(
        !any(duplicateGroups),
        paste0(
            "Requirement estimates must contain exactly one row per ",
            paste(identityColumns, collapse = " + "),
            "."
        ),
        "acmm_analysis_api_error"
    )

    invisible(TRUE)
}

acmm_dimension_score <- function(scores, method)
{
    method <- acmm_validate_dimension_method(method)
    acmm_assert(
        is.numeric(scores) && length(scores) > 0 && !anyNA(scores) &&
            all(is.finite(scores)) && all(scores >= 0 & scores <= 1),
        "Dimension scores must be finite numeric values in [0, 1].",
        "acmm_analysis_api_error"
    )

    if (method == "arithmetic")
    {
        return(mean(scores))
    }

    if (method == "geometric")
    {
        if (any(scores == 0))
        {
            return(0)
        }
        return(exp(mean(log(scores))))
    }

    if (method == "harmonic")
    {
        if (any(scores == 0))
        {
            return(0)
        }
        return(length(scores) / sum(1 / scores))
    }

    min(scores)
}

aggregate_acmm_dimension <- function(
    requirementEstimates,
    method,
    groupBy = acmmDefaultDimensionGroupBy
)
{
    method <- acmm_validate_dimension_method(method)
    acmm_validate_requirement_estimates(requirementEstimates, groupBy)

    if (nrow(requirementEstimates) == 0)
    {
        return(data.frame())
    }

    groups <- acmm_group_rows(requirementEstimates, groupBy)
    rows <- lapply(groups, function(groupRows)
    {
        groupValues <- acmm_group_values(requirementEstimates, groupRows, groupBy)
        scores <- requirementEstimates$p_hat[groupRows]
        missingCount <- sum(is.na(scores))
        estimableCount <- length(scores) - missingCount

        if (missingCount > 0)
        {
            score <- NA_real_
            status <- "incomplete_requirement_estimates"
        }
        else
        {
            score <- acmm_dimension_score(scores, method)
            status <- "ok"
        }

        c(
            groupValues,
            list(
                aggregation_method = method,
                weighting = "equal",
                total_requirements = length(scores),
                estimable_requirements = estimableCount,
                missing_requirements = missingCount,
                s_d = score,
                aggregation_status = status
            )
        )
    })
    acmm_bind_rows(rows)
}

aggregate_acmm_dimensions <- function(
    requirementEstimates,
    methods = acmmDimensionMethods,
    groupBy = acmmDefaultDimensionGroupBy
)
{
    methods <- acmm_validate_dimension_methods(methods)
    acmm_validate_requirement_estimates(requirementEstimates, groupBy)

    tables <- lapply(methods, function(method)
    {
        aggregate_acmm_dimension(requirementEstimates, method, groupBy)
    })

    nonEmpty <- vapply(tables, nrow, integer(1)) > 0
    if (!any(nonEmpty))
    {
        return(data.frame())
    }

    result <- do.call(rbind, tables[nonEmpty])
    rownames(result) <- NULL
    result
}

estimate_acmm_dimension_scores <- function(
    population,
    methods = acmmDimensionMethods,
    requirementGroupBy = acmmDefaultGroupBy,
    dimensionGroupBy = acmmDefaultDimensionGroupBy
)
{
    estimates <- estimate_acmm_compliance(population, requirementGroupBy)

    list(
        opportunity = aggregate_acmm_dimensions(
            estimates$opportunity,
            methods,
            dimensionGroupBy
        ),
        run = aggregate_acmm_dimensions(
            estimates$run,
            methods,
            dimensionGroupBy
        ),
        scenario = aggregate_acmm_dimensions(
            estimates$scenario,
            methods,
            dimensionGroupBy
        )
    )
}
