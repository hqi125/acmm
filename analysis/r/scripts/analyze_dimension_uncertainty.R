commandArguments <- commandArgs(trailingOnly = FALSE)
fileArgument <- grep("^--file=", commandArguments, value = TRUE)

if (length(fileArgument) != 1)
{
    stop("Could not resolve analyze_dimension_uncertainty.R script path.")
}

scriptPath <- sub("^--file=", "", fileArgument[[1]])
analysisRoot <- dirname(dirname(normalizePath(scriptPath, winslash = "/", mustWork = TRUE)))
source(file.path(analysisRoot, "R", "bootstrap.R"))
source_acmm_analysis(analysisRoot)

parsedArguments <- acmm_extract_population_policy_argument(
    commandArgs(trailingOnly = TRUE)
)
arguments <- parsedArguments$arguments
populationPolicy <- parsedArguments$policy
if (!(length(arguments) %in% c(3, 4, 5)))
{
    stop(
        paste0(
            "Usage: Rscript analysis/r/scripts/analyze_dimension_uncertainty.R ",
            "<export-directory> <bootstrap-replicates> <seed> [confidence-level] [csv-output]"
        )
    )
}

bootstrapReplicates <- acmm_parse_integer(
    arguments[[2]],
    "bootstrap_replicates",
    minimum = 1
)
seed <- acmm_parse_integer(arguments[[3]], "seed", minimum = 0)
confidenceLevel <- if (length(arguments) >= 4) {
    acmm_parse_number(arguments[[4]], "confidence_level")
} else {
    0.95
}

exportData <- load_acmm_export(
    arguments[[1]],
    populationPolicy = populationPolicy
)
population <- build_acmm_population(
    exportData,
    populationPolicy = populationPolicy
)

cat("ACMM dimension-uncertainty input validated successfully.\n")
cat("Execution:", exportData$binding$execution_id, "\n")
cat("Experiment:", exportData$binding$experiment_id, "\n")
print_acmm_population_summary(population)
cat("Bootstrap replicates:", bootstrapReplicates, "\n")
cat("Seed:", seed, "\n")
cat("Confidence level:", confidenceLevel, "\n")
cat("Weighting: equal\n")
cat("Methods:", paste(acmmDimensionMethods, collapse = ", "), "\n")
cat("Computing hierarchical dimension-score intervals...\n")
flush(stdout())

intervals <- interval_acmm_dimension_scores_hierarchical(
    population,
    confidenceLevel = confidenceLevel,
    bootstrapReplicates = bootstrapReplicates,
    seed = seed
)

cat("Hierarchical dimension-score interval computation complete.\n\n")
if (length(arguments) == 5)
{
    write_acmm_analysis_csv(intervals, arguments[[5]])
}

cat("Opportunity-weighted dimension-score intervals:\n")
print(intervals$opportunity, row.names = FALSE)
cat("\nRun-weighted dimension-score intervals:\n")
print(intervals$run, row.names = FALSE)
cat("\nScenario-weighted dimension-score intervals:\n")
print(intervals$scenario, row.names = FALSE)
