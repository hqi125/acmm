commandArguments <- commandArgs(trailingOnly = FALSE)
fileArgument <- grep("^--file=", commandArguments, value = TRUE)

if (length(fileArgument) != 1)
{
    stop("Could not resolve analyze_availability.R script path.")
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

if (length(arguments) != 5)
{
    stop(
        paste0(
            "Usage: Rscript analysis/r/scripts/analyze_availability.R ",
            "<export-directory> <bootstrap-replicates> <seed> ",
            "<confidence-level> <output-directory> ",
            "[--population-policy=strict|available-valid]"
        )
    )
}

exportDir <- arguments[[1]]
bootstrapReplicates <- acmm_parse_integer(
    arguments[[2]],
    "bootstrap_replicates",
    minimum = 1
)
seed <- acmm_parse_integer(arguments[[3]], "seed", minimum = 0)
confidenceLevel <- acmm_parse_number(arguments[[4]], "confidence_level")
outputRoot <- arguments[[5]]
dataDir <- file.path(outputRoot, "data", "availability")

exportData <- load_acmm_export(
    exportDir,
    populationPolicy = populationPolicy
)
availability <- analyze_acmm_availability(
    exportData,
    confidenceLevel = confidenceLevel,
    bootstrapReplicates = bootstrapReplicates,
    seed = seed
)

if (!dir.exists(dataDir))
{
    dir.create(dataDir, recursive = TRUE, showWarnings = FALSE)
}

tables <- list(
    availability_summary = availability$summary,
    availability_cells = availability$cells,
    availability_positions = availability$positions,
    availability_slots = availability$slots,
    availability_failure_modes = availability$failure_modes,
    availability_attempt_outcomes = availability$attempt_outcomes,
    availability_intervals = availability$intervals,
    availability_sensitivity = availability$sensitivity
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

cat("ACMM availability analysis completed successfully.\n")
print_acmm_export_source(exportData$binding)
cat("Experiment:", exportData$binding$experiment_id, "\n")
cat("Population policy:", populationPolicy, "\n")
cat("Bootstrap replicates:", bootstrapReplicates, "\n")
cat("Seed:", seed, "\n")
cat("Confidence level:", confidenceLevel, "\n\n")

cat("Availability summary:\n")
print(availability$summary, row.names = FALSE)
cat("\nSubject/scenario availability:\n")
print(availability$cells, row.names = FALSE)
cat("\nRecorded final failure modes for unavailable slots:\n")
if (nrow(availability$failure_modes) == 0)
{
    cat("No unavailable planned slots.\n")
} else
{
    print(availability$failure_modes, row.names = FALSE)
}
cat("\nHierarchical availability intervals:\n")
print(availability$intervals, row.names = FALSE)
cat("\nCompliance sensitivity envelope:\n")
if (nrow(availability$sensitivity) == 0)
{
    cat("No sensitivity rows were estimable.\n")
} else
{
    print(availability$sensitivity, row.names = FALSE)
}
cat(
    "\nNOTE: failure_kind and termination are recorded operational surfaces, not ",
    "causal attributions. Missing planned slots remain missing observations; they ",
    "are never converted into compliance violations. Sensitivity bounds are ",
    "deterministic extreme-case envelopes, not confidence intervals.\n",
    sep = ""
)
