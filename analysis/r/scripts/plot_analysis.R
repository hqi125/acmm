commandArguments <- commandArgs(trailingOnly = FALSE)
fileArgument <- grep("^--file=", commandArguments, value = TRUE)

if (length(fileArgument) != 1)
{
    stop("Could not resolve plot_analysis.R script path.")
}

scriptPath <- sub("^--file=", "", fileArgument[[1]])
analysisRoot <- dirname(dirname(normalizePath(scriptPath, winslash = "/", mustWork = TRUE)))
source(file.path(analysisRoot, "R", "bootstrap.R"))
source_acmm_analysis(analysisRoot)

arguments <- commandArgs(trailingOnly = TRUE)
if (length(arguments) < 3)
{
    stop(
        paste0(
            "Usage: Rscript analysis/r/scripts/plot_analysis.R ",
            "<analysis-output-directory> <plot-kind> <formats> [plot-arguments...]"
        )
    )
}

outputRoot <- normalizePath(arguments[[1]], winslash = "/", mustWork = TRUE)
plotKind <- arguments[[2]]
formats <- strsplit(arguments[[3]], ",", fixed = TRUE)[[1]]
formats <- acmm_normalize_plot_formats(formats)
plotArguments <- if (length(arguments) > 3) arguments[4:length(arguments)] else character(0)

cat("ACMM_PROGRESS: Rendering R plot '", plotKind, "'...\n", sep = "")
flush(stdout())

if (identical(plotKind, "run_duration"))
{
    acmm_assert(
        length(plotArguments) >= 2,
        "run_duration plot requires export directory and population policy arguments."
    )
    exportData <- load_acmm_export(
        plotArguments[[1]],
        populationPolicy = plotArguments[[2]]
    )
    acmm_plot_run_duration(exportData, outputRoot, plotArguments[[2]], formats)
} else if (identical(plotKind, "requirement_compliance"))
{
    estimand <- if (length(plotArguments) >= 1) plotArguments[[1]] else "scenario"
    includeIntervals <- if (length(plotArguments) >= 2) identical(plotArguments[[2]], "true") else TRUE
    acmm_plot_requirement_compliance(outputRoot, estimand, includeIntervals, formats)
} else if (identical(plotKind, "dimension_scores"))
{
    estimand <- if (length(plotArguments) >= 1) plotArguments[[1]] else "opportunity"
    aggregationMethod <- if (length(plotArguments) >= 2) plotArguments[[2]] else "arithmetic"
    includeIntervals <- if (length(plotArguments) >= 3) identical(plotArguments[[3]], "true") else TRUE
    acmm_plot_dimension_scores(outputRoot, estimand, aggregationMethod, includeIntervals, formats)
} else if (identical(plotKind, "dimension_profile"))
{
    estimand <- if (length(plotArguments) >= 1) plotArguments[[1]] else "opportunity"
    aggregationMethod <- if (length(plotArguments) >= 2) plotArguments[[2]] else "arithmetic"
    acmm_plot_dimension_profile(outputRoot, estimand, aggregationMethod, formats)
} else if (identical(plotKind, "availability"))
{
    acmm_plot_availability(outputRoot, formats)
} else if (identical(plotKind, "trajectory_run_metrics"))
{
    metric <- if (length(plotArguments) >= 1) plotArguments[[1]] else "tool_calls"
    acmm_plot_trajectory_run_metrics(outputRoot, metric, formats)
} else if (identical(plotKind, "trajectory_tool_usage"))
{
    topN <- if (length(plotArguments) >= 1) as.integer(plotArguments[[1]]) else 12L
    acmm_assert(!is.na(topN) && topN >= 1, "trajectory_tool_usage top_n must be positive.")
    acmm_plot_trajectory_tool_usage(outputRoot, topN, formats)
} else if (identical(plotKind, "trajectory_error_rate"))
{
    acmm_plot_trajectory_error_rate(outputRoot, formats)
} else if (identical(plotKind, "trajectory_tool_composition"))
{
    topN <- if (length(plotArguments) >= 1) as.integer(plotArguments[[1]]) else 12L
    acmm_assert(!is.na(topN) && topN >= 1, "trajectory_tool_composition top_n must be positive.")
    acmm_plot_trajectory_tool_composition(outputRoot, topN, formats)
} else if (identical(plotKind, "provider_pressure_pacing"))
{
    acmm_plot_provider_pressure_pacing(outputRoot, formats)
} else if (identical(plotKind, "provider_pressure_feature_scores"))
{
    topN <- if (length(plotArguments) >= 1) as.integer(plotArguments[[1]]) else 8L
    acmm_assert(!is.na(topN) && topN >= 1, "provider_pressure_feature_scores top_n must be positive.")
    acmm_plot_provider_pressure_feature_scores(outputRoot, topN, formats)
} else if (identical(plotKind, "provider_pressure_admission_waits"))
{
    acmm_plot_provider_pressure_admission_waits(outputRoot, formats)
} else if (identical(plotKind, "provider_pressure_utilization_envelope"))
{
    acmm_plot_provider_pressure_utilization_envelope(outputRoot, formats)
} else if (identical(plotKind, "provider_pressure_utilization_by_subject"))
{
    acmm_plot_provider_pressure_utilization_by_subject(outputRoot, formats)
} else if (identical(plotKind, "provider_pressure_wait_vs_actions"))
{
    acmm_plot_provider_pressure_wait_vs_actions(outputRoot, formats)
} else
{
    stop(paste0("Unsupported plot kind '", plotKind, "'."))
}

cat(
    "ACMM_PROGRESS: R plot '",
    plotKind,
    "' complete (",
    paste(toupper(formats), collapse = " + "),
    ").\n",
    sep = ""
)
flush(stdout())
