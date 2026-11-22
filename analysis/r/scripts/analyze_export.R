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

parsedArguments <- acmm_extract_population_policy_argument(
    commandArgs(trailingOnly = TRUE)
)
arguments <- parsedArguments$arguments
populationPolicy <- parsedArguments$policy
if (!(length(arguments) %in% c(1, 2)))
{
    stop(
        paste0(
            "Usage: Rscript analysis/r/scripts/analyze_export.R ",
            "<export-directory> [csv-output]"
        )
    )
}

exportData <- load_acmm_export(
    arguments[[1]],
    populationPolicy = populationPolicy
)
population <- build_acmm_population(
    exportData,
    populationPolicy = populationPolicy
)
estimates <- estimate_acmm_compliance(population)

if (length(arguments) == 2)
{
    write_acmm_analysis_csv(estimates, arguments[[2]])
    write_acmm_population_tables(population, dirname(arguments[[2]]))
}

cat("ACMM analysis input validated successfully.\n")
print_acmm_export_source(exportData$binding)
cat("Experiment:", exportData$binding$experiment_id, "\n")
print_acmm_population_summary(population)
cat("Excluded attempt rows:", nrow(population$excluded_attempts), "\n\n")

cat("Opportunity-weighted requirement estimates:\n")
print(estimates$opportunity, row.names = FALSE)
cat("\nRun-weighted requirement estimates:\n")
print(estimates$run, row.names = FALSE)
cat("\nScenario-weighted requirement estimates:\n")
print(estimates$scenario, row.names = FALSE)
