commandArguments <- commandArgs(trailingOnly = FALSE)
fileArgument <- grep("^--file=", commandArguments, value = TRUE)

if (length(fileArgument) != 1)
{
    stop("Could not resolve analyze_interactions.R script path.")
}

scriptPath <- sub("^--file=", "", fileArgument[[1]])
analysisRoot <- dirname(dirname(normalizePath(scriptPath, winslash = "/", mustWork = TRUE)))
source(file.path(analysisRoot, "R", "bootstrap.R"))
source_acmm_analysis(analysisRoot)

arguments <- commandArgs(trailingOnly = TRUE)
policyArgument <- acmm_extract_population_policy_argument(arguments)
arguments <- policyArgument$arguments
populationPolicy <- policyArgument$policy

if (length(arguments) != 6)
{
    stop(
        paste0(
            "Usage: Rscript analyze_interactions.R <export-dir> <bootstrap-replicates> ",
            "<seed> <confidence-level> <output-dir> <experiment-id> ",
            "[--population-policy=strict|available-valid]"
        )
    )
}

exportDir <- arguments[[1]]
bootstrapReplicates <- as.numeric(arguments[[2]])
seed <- as.numeric(arguments[[3]])
confidenceLevel <- as.numeric(arguments[[4]])
outputDir <- arguments[[5]]
experimentId <- arguments[[6]]

supportedExperiment <- "controlled_recovery_boundary_interaction_v1"
acmm_assert(
    identical(experimentId, supportedExperiment),
    paste0(
        "Interaction analysis currently supports only '", supportedExperiment,
        "'; received '", experimentId, "'."
    ),
    "acmm_interaction_design_error"
)

exportData <- load_acmm_export(exportDir, populationPolicy = populationPolicy)
population <- build_acmm_population(exportData, populationPolicy = populationPolicy)

factorSpec <- list(
    ordinary_write_outcome = list(
        json_key = "ordinary_write_mode",
        low_value = "success",
        high_value = "failure",
        low_label = "success",
        high_label = "failure"
    ),
    authorized_recovery = list(
        json_key = "authorized_recovery_available",
        low_value = FALSE,
        high_value = TRUE,
        low_label = "unavailable",
        high_label = "available"
    )
)

interactionData <- acmm_interaction_attach_factors(population, factorSpec)
cellEstimates <- acmm_interaction_cell_estimates(interactionData)
simpleEffects <- acmm_interaction_simple_effects(interactionData, cellEstimates)
interactionTable <- interval_acmm_interaction_block_bootstrap(
    interactionData,
    bootstrapReplicates = bootstrapReplicates,
    seed = seed,
    confidenceLevel = confidenceLevel
)

if (!dir.exists(outputDir))
{
    dir.create(outputDir, recursive = TRUE, showWarnings = FALSE)
}

write_acmm_interaction_csv(
    population$summary,
    file.path(outputDir, "interaction_population_summary.csv")
)
write_acmm_interaction_csv(
    population$cell_coverage,
    file.path(outputDir, "interaction_population_cell_coverage.csv")
)
write_acmm_interaction_csv(
    population$missing_runs,
    file.path(outputDir, "interaction_population_missing_runs.csv")
)
write_acmm_interaction_csv(
    cellEstimates,
    file.path(outputDir, "interaction_cell_estimates.csv")
)
write_acmm_interaction_csv(
    simpleEffects,
    file.path(outputDir, "interaction_simple_effects.csv")
)
write_acmm_interaction_csv(
    interactionTable,
    file.path(outputDir, "interaction_contrasts.csv")
)

figureDir <- file.path(outputDir, "figures", "interaction")
figureSpecs <- list(
    list(
        name = "boundary_cell_rates",
        dimension = "boundary",
        file = "boundary_cell_rates.png",
        title = "Boundary compliance across the 2 x 2 interaction design"
    ),
    list(
        name = "recovery_cell_rates",
        dimension = "recovery",
        file = "recovery_cell_rates.png",
        title = "Recovery compliance across the 2 x 2 interaction design"
    ),
    list(
        name = "procedure_cell_rates",
        dimension = "procedure",
        file = "procedure_cell_rates.png",
        title = "Procedure compliance across the 2 x 2 interaction design"
    ),
    list(
        name = "goal_feasible_cell_rates",
        dimension = "goal",
        file = "goal_feasible_cell_rates.png",
        title = "Goal compliance in applicable Experiment 3 cells"
    )
)

figureRows <- list()
figureIndex <- 1
for (spec in figureSpecs)
{
    path <- file.path(figureDir, spec$file)
    generated <- acmm_interaction_plot_cells(
        cellEstimates,
        interactionData,
        spec$dimension,
        path,
        spec$title
    )
    figureRows[[figureIndex]] <- data.frame(
        figure_name = spec$name,
        title = spec$title,
        relative_path = file.path("figures", "interaction", spec$file),
        status = if (generated) "generated" else "not_generated_no_data",
        stringsAsFactors = FALSE
    )
    figureIndex <- figureIndex + 1
}

contrastPath <- file.path(figureDir, "interaction_contrasts.png")
contrastGenerated <- acmm_interaction_plot_contrasts(interactionTable, contrastPath)
figureRows[[figureIndex]] <- data.frame(
    figure_name = "interaction_contrasts",
    title = "Recovery x ordinary-write interaction contrasts",
    relative_path = file.path("figures", "interaction", "interaction_contrasts.png"),
    status = if (contrastGenerated) "generated" else "not_generated_no_data",
    stringsAsFactors = FALSE
)

figureManifest <- do.call(rbind, figureRows)
rownames(figureManifest) <- NULL
write_acmm_interaction_csv(
    figureManifest,
    file.path(outputDir, "interaction_figures.csv")
)

cat("ACMM interaction analysis completed successfully.\n")
cat("Experiment:", experimentId, "\n")
print_acmm_population_summary(population)
cat("Factor A: ordinary write outcome (success -> failure)\n")
cat("Factor B: authorized recovery (unavailable -> available)\n")
cat(
    "Interaction estimand: [B effect under failure] - [B effect under success].\n"
)
cat(
    "Uncertainty: repetition-block percentile bootstrap when observed outcomes vary; ",
    "degenerate all-equal outcomes are reported without a zero-width bootstrap interval.\n",
    sep = ""
)
cat("\nInteraction contrasts:\n")
print(interactionTable, row.names = FALSE)
