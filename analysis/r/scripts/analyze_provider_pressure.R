commandArguments <- commandArgs(trailingOnly = FALSE)
fileArgument <- grep("^--file=", commandArguments, value = TRUE)

if (length(fileArgument) != 1)
{
    stop("Could not resolve analyze_provider_pressure.R script path.")
}

scriptPath <- sub("^--file=", "", fileArgument[[1]])
analysisRoot <- dirname(dirname(normalizePath(scriptPath, winslash = "/", mustWork = TRUE)))
source(file.path(analysisRoot, "R", "bootstrap.R"))
source_acmm_analysis(analysisRoot)

arguments <- commandArgs(trailingOnly = TRUE)
if (length(arguments) != 2)
{
    stop(
        paste0(
            "Usage: Rscript analysis/r/scripts/analyze_provider_pressure.R ",
            "<provider-pressure-export-directory> <output-directory>"
        )
    )
}

exportDir <- arguments[[1]]
outputRoot <- arguments[[2]]
dataDir <- file.path(outputRoot, "data", "provider_pressure")

cat("ACMM_PROGRESS: Summarizing Provider Pressure trajectory tables...\n")
flush(stdout())
summary <- acmm_provider_pressure_summary(
    exportDir,
    progress = function(message)
    {
        cat("ACMM_PROGRESS: ", message, "\n", sep = "")
        flush(stdout())
    }
)
cat("ACMM_PROGRESS: Provider Pressure trajectory summary complete.\n")
flush(stdout())

if (!dir.exists(dataDir))
{
    dir.create(dataDir, recursive = TRUE, showWarnings = FALSE)
}

tables <- list(
    provider_pressure_snapshot_summary = summary$snapshot_summary,
    provider_pressure_pacing_summary = summary$pacing_summary,
    provider_pressure_feature_summary = summary$feature_summary,
    provider_pressure_feature_trajectory = summary$feature_trajectory,
    provider_pressure_admission_summary = summary$admission_summary,
    provider_pressure_utilization_summary = summary$utilization_summary
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

cat("Provider Pressure analysis completed successfully.\n")
cat("Snapshots:", nrow(summary$snapshot_summary), "\n")
cat("Pacing groups:", nrow(summary$pacing_summary), "\n")
cat("Final feature states:", nrow(summary$feature_summary), "\n")
cat("Run admission summaries:", nrow(summary$admission_summary), "\n")
cat("Run utilization summaries:", nrow(summary$utilization_summary), "\n")
