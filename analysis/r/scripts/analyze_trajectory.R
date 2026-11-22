commandArguments <- commandArgs(trailingOnly = FALSE)
fileArgument <- grep("^--file=", commandArguments, value = TRUE)

if (length(fileArgument) != 1)
{
    stop("Could not resolve analyze_trajectory.R script path.")
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

if (length(arguments) != 2)
{
    stop(
        paste0(
            "Usage: Rscript analysis/r/scripts/analyze_trajectory.R ",
            "<export-directory> <output-directory> ",
            "[--population-policy=strict|available-valid]"
        )
    )
}

exportDir <- arguments[[1]]
outputRoot <- arguments[[2]]
dataDir <- file.path(outputRoot, "data", "trajectory")

exportData <- load_acmm_export(
    exportDir,
    populationPolicy = populationPolicy
)
cat("ACMM_PROGRESS: Computing normalized tool trajectory metrics...\n")
flush(stdout())
trajectory <- analyze_acmm_trajectory(
    exportData,
    populationPolicy = populationPolicy
)
cat("ACMM_PROGRESS: Tool trajectory metric computation complete.\n")
flush(stdout())

if (!dir.exists(dataDir))
{
    dir.create(dataDir, recursive = TRUE, showWarnings = FALSE)
}

tables <- list(
    trajectory_run_metrics = trajectory$run_metrics,
    trajectory_subject_summary = trajectory$subject_summary,
    trajectory_tool_usage = trajectory$tool_usage
)

for (tableName in names(tables))
{
    utils::write.csv(
        tables[[tableName]],
        file = file.path(dataDir, paste0(tableName, ".csv")),
        row.names = FALSE,
        na = ""
    )
}

cat("ACMM trajectory analysis completed successfully.\n")
print_acmm_export_source(exportData$binding)
cat("Experiment:", exportData$binding$experiment_id, "\n")
cat("Population policy:", populationPolicy, "\n")
cat("Selected runs:", nrow(trajectory$run_metrics), "\n\n")
cat("Subject trajectory summary:\n")
print(trajectory$subject_summary, row.names = FALSE)
