commandArguments <- commandArgs(trailingOnly = FALSE)
fileArgument <- grep("^--file=", commandArguments, value = TRUE)

if (length(fileArgument) != 1)
{
    stop("Could not resolve analyze_dimensions.R script path.")
}

scriptPath <- sub("^--file=", "", fileArgument[[1]])
analysisRoot <- dirname(dirname(normalizePath(scriptPath, winslash = "/", mustWork = TRUE)))
source(file.path(analysisRoot, "R", "bootstrap.R"))
source_acmm_analysis(analysisRoot)

arguments <- commandArgs(trailingOnly = TRUE)
if (!(length(arguments) %in% c(1, 2)))
{
    stop(
        paste0(
            "Usage: Rscript analysis/r/scripts/analyze_dimensions.R ",
            "<export-directory> [csv-output]"
        )
    )
}

exportData <- load_acmm_export(arguments[[1]])
population <- build_acmm_population(exportData)
scores <- estimate_acmm_dimension_scores(population)

if (length(arguments) == 2)
{
    write_acmm_analysis_csv(scores, arguments[[2]])
}

cat("ACMM dimension-score input validated successfully.\n")
cat("Execution:", exportData$binding$execution_id, "\n")
cat("Experiment:", exportData$binding$experiment_id, "\n")
cat("Weighting: equal\n")
cat("Methods:", paste(acmmDimensionMethods, collapse = ", "), "\n\n")

cat("Opportunity-weighted dimension scores:\n")
print(scores$opportunity, row.names = FALSE)
cat("\nRun-weighted dimension scores:\n")
print(scores$run, row.names = FALSE)
cat("\nScenario-weighted dimension scores:\n")
print(scores$scenario, row.names = FALSE)
