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
            "<seed> <confidence-level> <output-dir> <interaction-design> ",
            "[--population-policy=strict|available-valid]"
        )
    )
}

exportDir <- arguments[[1]]
bootstrapReplicates <- as.numeric(arguments[[2]])
seed <- as.numeric(arguments[[3]])
confidenceLevel <- as.numeric(arguments[[4]])
outputRoot <- arguments[[5]]
dataDir <- file.path(outputRoot, "data", "interaction")
designPath <- arguments[[6]]

acmm_require_namespace("yaml")
acmm_assert(
    file.exists(designPath),
    paste0("Interaction design file does not exist: '", designPath, "'."),
    "acmm_input_error"
)
design <- yaml::read_yaml(designPath)
acmm_assert(
    is.list(design),
    "Interaction design must contain a YAML mapping.",
    "acmm_interaction_design_error"
)
acmm_assert(
    identical(as.character(design$schema_version), "0.1"),
    "Interaction design schema_version must be '0.1'.",
    "acmm_interaction_design_error"
)
acmm_assert(
    identical(as.character(design$analysis_kind), "interaction"),
    "Interaction design analysis_kind must be 'interaction'.",
    "acmm_interaction_design_error"
)
acmm_assert(
    is.character(design$experiment_id) && length(design$experiment_id) == 1 &&
        nzchar(design$experiment_id),
    "Interaction design must define one non-empty experiment_id.",
    "acmm_interaction_design_error"
)
acmm_assert(
    is.list(design$factors) && length(design$factors) == 2 &&
        !is.null(names(design$factors)),
    "Interaction design must define exactly two named factors.",
    "acmm_interaction_design_error"
)

for (factorName in names(design$factors))
{
    factor <- design$factors[[factorName]]
    acmm_assert(
        is.list(factor),
        paste0("Interaction factor '", factorName, "' must be a mapping."),
        "acmm_interaction_design_error"
    )
    for (field in c("display_name", "json_key", "low_label", "high_label"))
    {
        value <- factor[[field]]
        acmm_assert(
            is.character(value) && length(value) == 1 && nzchar(value),
            paste0(
                "Interaction factor '", factorName,
                "' requires one non-empty '", field, "'."
            ),
            "acmm_interaction_design_error"
        )
    }
    acmm_assert(
        !is.null(factor$low_value) && !is.null(factor$high_value),
        paste0(
            "Interaction factor '", factorName,
            "' requires low_value and high_value."
        ),
        "acmm_interaction_design_error"
    )
}

acmm_assert(
    is.list(design$estimand),
    "Interaction design must define an estimand mapping.",
    "acmm_interaction_design_error"
)
for (field in c("primary_contrast", "interpretation"))
{
    value <- design$estimand[[field]]
    acmm_assert(
        is.character(value) && length(value) == 1 && nzchar(value),
        paste0("Interaction estimand requires one non-empty '", field, "'."),
        "acmm_interaction_design_error"
    )
}

exportData <- load_acmm_export(exportDir, populationPolicy = populationPolicy)
experimentId <- exportData$binding$experiment_id
acmm_assert(
    identical(experimentId, as.character(design$experiment_id)),
    paste0(
        "Interaction design experiment_id '", design$experiment_id,
        "' does not match export experiment '", experimentId, "'."
    ),
    "acmm_interaction_design_error"
)
population <- build_acmm_population(exportData, populationPolicy = populationPolicy)

factorSpec <- design$factors
interactionData <- acmm_interaction_attach_factors(population, factorSpec)
cellEstimates <- acmm_interaction_cell_estimates(interactionData)
simpleEffects <- acmm_interaction_simple_effects(interactionData, cellEstimates)
cat("ACMM_PROGRESS: Computing block-bootstrap interaction intervals...\n")
flush(stdout())
interactionTable <- interval_acmm_interaction_block_bootstrap(
    interactionData,
    bootstrapReplicates = bootstrapReplicates,
    seed = seed,
    confidenceLevel = confidenceLevel
)
cat("ACMM_PROGRESS: Interaction bootstrap computation complete.\n")
flush(stdout())

if (!dir.exists(dataDir))
{
    dir.create(dataDir, recursive = TRUE, showWarnings = FALSE)
}

write_acmm_interaction_csv(
    population$summary,
    file.path(dataDir, "interaction_population_summary.csv")
)
write_acmm_interaction_csv(
    population$cell_coverage,
    file.path(dataDir, "interaction_population_cell_coverage.csv")
)
write_acmm_interaction_csv(
    population$missing_runs,
    file.path(dataDir, "interaction_population_missing_runs.csv")
)
write_acmm_interaction_csv(
    cellEstimates,
    file.path(dataDir, "interaction_cell_estimates.csv")
)
write_acmm_interaction_csv(
    simpleEffects,
    file.path(dataDir, "interaction_simple_effects.csv")
)
write_acmm_interaction_csv(
    interactionTable,
    file.path(dataDir, "interaction_contrasts.csv")
)

factorNames <- names(factorSpec)
factorA <- factorSpec[[factorNames[[1]]]]
factorB <- factorSpec[[factorNames[[2]]]]
figureDir <- file.path(outputRoot, "figures", "interaction")
cellFigureSpecs <- design$figures$cell_rates
if (is.null(cellFigureSpecs))
{
    cellFigureSpecs <- list()
}

figureRows <- list()
figureIndex <- 1
for (spec in cellFigureSpecs)
{
    acmm_assert(
        is.list(spec) &&
            is.character(spec$name) && length(spec$name) == 1 &&
            is.character(spec$dimension) && length(spec$dimension) == 1 &&
            is.character(spec$file) && length(spec$file) == 1 &&
            is.character(spec$title) && length(spec$title) == 1,
        "Each interaction cell-rate figure must define name, dimension, file, and title.",
        "acmm_interaction_design_error"
    )
    path <- file.path(figureDir, spec$file)
    generated <- acmm_interaction_plot_cells(
        cellEstimates,
        interactionData,
        spec$dimension,
        path,
        spec$title,
        factorA$display_name,
        factorB$display_name
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

contrastSpec <- design$figures$contrast
if (!is.null(contrastSpec))
{
    acmm_assert(
        is.list(contrastSpec) &&
            is.character(contrastSpec$name) && length(contrastSpec$name) == 1 &&
            is.character(contrastSpec$file) && length(contrastSpec$file) == 1 &&
            is.character(contrastSpec$title) && length(contrastSpec$title) == 1,
        "Interaction contrast figure must define name, file, and title.",
        "acmm_interaction_design_error"
    )
    contrastPath <- file.path(figureDir, contrastSpec$file)
    contrastGenerated <- acmm_interaction_plot_contrasts(
        interactionTable,
        contrastPath,
        contrastSpec$title
    )
    figureRows[[figureIndex]] <- data.frame(
        figure_name = contrastSpec$name,
        title = contrastSpec$title,
        relative_path = file.path("figures", "interaction", contrastSpec$file),
        status = if (contrastGenerated) "generated" else "not_generated_no_data",
        stringsAsFactors = FALSE
    )
}

if (length(figureRows) == 0)
{
    figureManifest <- data.frame(
        figure_name = character(0),
        title = character(0),
        relative_path = character(0),
        status = character(0),
        stringsAsFactors = FALSE
    )
} else {
    figureManifest <- do.call(rbind, figureRows)
    rownames(figureManifest) <- NULL
}
write_acmm_interaction_csv(
    figureManifest,
    file.path(dataDir, "interaction_figures.csv")
)

cat("ACMM interaction analysis completed successfully.\n")
cat("Experiment:", experimentId, "\n")
print_acmm_population_summary(population)
cat(
    "Factor A:", factorA$display_name,
    "(", factorA$low_label, "->", factorA$high_label, ")\n"
)
cat(
    "Factor B:", factorB$display_name,
    "(", factorB$low_label, "->", factorB$high_label, ")\n"
)
cat("Interaction estimand:", design$estimand$primary_contrast, "\n")
cat(
    "Uncertainty: repetition-block percentile bootstrap when observed outcomes vary; ",
    "degenerate all-equal outcomes are reported without a zero-width bootstrap interval.\n",
    sep = ""
)
cat("\nInteraction contrasts:\n")
print(interactionTable, row.names = FALSE)
