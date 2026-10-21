commandArguments <- commandArgs(trailingOnly = FALSE)
fileArgument <- grep("^--file=", commandArguments, value = TRUE)

if (length(fileArgument) != 1)
{
    stop("Could not resolve analyze_export.R script path.")
}

scriptPath <- sub("^--file=", "", fileArgument[[1]])
analysisRoot <- dirname(dirname(normalizePath(scriptPath, winslash = "/", mustWork = TRUE)))
source(file.path(analysisRoot, "R", "bootstrap.R"))
source_acmm_analysis(analysisRoot)

arguments <- commandArgs(trailingOnly = TRUE)
if (length(arguments) != 1)
{
    stop("Usage: Rscript analysis/r/scripts/analyze_export.R <export-directory>")
}

exportData <- load_acmm_export(arguments[[1]])
population <- build_acmm_population(exportData)
estimates <- estimate_acmm_compliance(population)

cat("ACMM analysis input validated successfully.\n")
cat("Execution:", exportData$binding$execution_id, "\n")
cat("Experiment:", exportData$binding$experiment_id, "\n")
cat("Selected valid runs:", nrow(population$runs), "\n")
cat("Excluded attempts:", nrow(population$excluded_attempts), "\n\n")

cat("Opportunity-weighted requirement estimates:\n")
print(estimates$opportunity, row.names = FALSE)
cat("\nRun-weighted requirement estimates:\n")
print(estimates$run, row.names = FALSE)
cat("\nScenario-weighted requirement estimates:\n")
print(estimates$scenario, row.names = FALSE)
