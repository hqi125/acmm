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
if (length(arguments) < 2)
{
    stop(
        paste0(
            "Usage: Rscript analysis/r/scripts/plot_analysis.R ",
            "<analysis-output-directory> <plot-kind> [plot-arguments...]"
        )
    )
}

outputRoot <- normalizePath(arguments[[1]], winslash = "/", mustWork = TRUE)
plotKind <- arguments[[2]]
plotArguments <- if (length(arguments) > 2) arguments[3:length(arguments)] else character(0)

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
    acmm_plot_run_duration(exportData, outputRoot, plotArguments[[2]])
} else if (identical(plotKind, "requirement_compliance"))
{
    estimand <- if (length(plotArguments) >= 1) plotArguments[[1]] else "scenario"
    includeIntervals <- if (length(plotArguments) >= 2) identical(plotArguments[[2]], "true") else TRUE
    acmm_plot_requirement_compliance(outputRoot, estimand, includeIntervals)
} else if (identical(plotKind, "dimension_scores"))
{
    estimand <- if (length(plotArguments) >= 1) plotArguments[[1]] else "opportunity"
    aggregationMethod <- if (length(plotArguments) >= 2) plotArguments[[2]] else "arithmetic"
    includeIntervals <- if (length(plotArguments) >= 3) identical(plotArguments[[3]], "true") else TRUE
    acmm_plot_dimension_scores(outputRoot, estimand, aggregationMethod, includeIntervals)
} else if (identical(plotKind, "availability"))
{
    acmm_plot_availability(outputRoot)
} else if (identical(plotKind, "trajectory_run_metrics"))
{
    metric <- if (length(plotArguments) >= 1) plotArguments[[1]] else "tool_calls"
    acmm_plot_trajectory_run_metrics(outputRoot, metric)
} else if (identical(plotKind, "trajectory_tool_usage"))
{
    topN <- if (length(plotArguments) >= 1) as.integer(plotArguments[[1]]) else 12L
    acmm_assert(!is.na(topN) && topN >= 1, "trajectory_tool_usage top_n must be positive.")
    acmm_plot_trajectory_tool_usage(outputRoot, topN)
} else
{
    stop(paste0("Unsupported plot kind '", plotKind, "'."))
}

cat("ACMM_PROGRESS: R plot '", plotKind, "' complete (PNG + PDF).\n", sep = "")
flush(stdout())
