acmm_analysis_source_order <- function()
{
    c(
        "conditions.R",
        "schema.R",
        "parse.R",
        "validate.R",
        "load_export.R",
        "population.R",
        "compliance.R",
        "aggregation.R",
        "uncertainty.R",
        "interaction.R",
        "reporting.R"
    )
}

source_acmm_analysis <- function(analysisRoot)
{
    analysisRoot <- normalizePath(analysisRoot, winslash = "/", mustWork = TRUE)
    sourceRoot <- file.path(analysisRoot, "R")

    if (!dir.exists(sourceRoot))
    {
        stop("analysisRoot does not contain an R/ source directory.")
    }

    for (fileName in acmm_analysis_source_order())
    {
        source(file.path(sourceRoot, fileName), local = .GlobalEnv)
    }
    invisible(TRUE)
}
