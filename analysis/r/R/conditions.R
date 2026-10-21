acmm_abort <- function(message, class = "acmm_analysis_error")
{
    condition <- structure(
        list(message = message, call = NULL),
        class = c(class, "acmm_analysis_error", "error", "condition")
    )
    stop(condition)
}

acmm_assert <- function(condition, message, class = "acmm_validation_error")
{
    if (!isTRUE(condition))
    {
        acmm_abort(message, class)
    }
}

acmm_require_namespace <- function(packageName)
{
    if (!requireNamespace(packageName, quietly = TRUE))
    {
        acmm_abort(
            paste0(
                "Required R package '", packageName, "' is not installed. ",
                "Install the declared analysis dependencies before continuing."
            ),
            "acmm_dependency_error"
        )
    }
}
