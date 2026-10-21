acmm_read_yaml_mapping <- function(path, label)
{
    acmm_require_namespace("yaml")
    acmm_assert(file.exists(path), paste0(label, " does not exist: '", path, "'."))

    value <- tryCatch(
        yaml::read_yaml(path),
        error = function(error)
        {
            acmm_abort(
                paste0("Could not parse ", label, " '", path, "': ", error$message),
                "acmm_input_error"
            )
        }
    )

    acmm_assert(
        is.list(value) && !is.null(names(value)),
        paste0(label, " must contain a YAML mapping."),
        "acmm_input_error"
    )
    value
}

acmm_read_csv_character <- function(path, expectedColumns, label)
{
    acmm_assert(file.exists(path), paste0(label, " does not exist: '", path, "'."))

    value <- tryCatch(
        utils::read.csv(
            path,
            header = TRUE,
            colClasses = "character",
            check.names = FALSE,
            stringsAsFactors = FALSE,
            na.strings = character(0),
            comment.char = "",
            strip.white = FALSE
        ),
        error = function(error)
        {
            acmm_abort(
                paste0("Could not parse ", label, " '", path, "': ", error$message),
                "acmm_input_error"
            )
        }
    )

    acmm_assert(
        identical(names(value), expectedColumns),
        paste0(
            label,
            " columns do not exactly match ACMM export schema ",
            acmmExportSchemaVersion,
            "."
        ),
        "acmm_schema_error"
    )
    value
}

acmm_parse_integer <- function(values, columnName, minimum = NULL)
{
    valid <- grepl("^-?(0|[1-9][0-9]*)$", values)
    acmm_assert(
        all(valid),
        paste0("Column '", columnName, "' must contain canonical integer strings."),
        "acmm_schema_error"
    )

    parsed <- suppressWarnings(as.numeric(values))
    acmm_assert(
        all(is.finite(parsed) & parsed == trunc(parsed)),
        paste0("Column '", columnName, "' contains an out-of-range integer."),
        "acmm_schema_error"
    )

    if (!is.null(minimum))
    {
        acmm_assert(
            all(parsed >= minimum),
            paste0("Column '", columnName, "' must be >= ", minimum, "."),
            "acmm_schema_error"
        )
    }
    parsed
}

acmm_parse_number <- function(values, columnName, minimum = NULL)
{
    pattern <- "^[+-]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?$"
    valid <- grepl(pattern, values)
    acmm_assert(
        all(valid),
        paste0("Column '", columnName, "' must contain finite numeric strings."),
        "acmm_schema_error"
    )

    parsed <- suppressWarnings(as.numeric(values))
    acmm_assert(
        all(is.finite(parsed)),
        paste0("Column '", columnName, "' contains a non-finite number."),
        "acmm_schema_error"
    )

    if (!is.null(minimum))
    {
        acmm_assert(
            all(parsed >= minimum),
            paste0("Column '", columnName, "' must be >= ", minimum, "."),
            "acmm_schema_error"
        )
    }
    parsed
}

acmm_parse_boolean <- function(values, columnName)
{
    acmm_assert(
        all(values %in% c("true", "false")),
        paste0("Column '", columnName, "' must contain only 'true' or 'false'."),
        "acmm_schema_error"
    )
    values == "true"
}

acmm_parse_timestamp <- function(values, columnName)
{
    pattern <- paste0(
        "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:",
        "[0-9]{2}(\\.[0-9]+)?Z$"
    )
    acmm_assert(
        all(grepl(pattern, values)),
        paste0("Column '", columnName, "' must contain UTC ISO-8601 timestamps."),
        "acmm_schema_error"
    )

    parsed <- as.POSIXct(
        values,
        format = "%Y-%m-%dT%H:%M:%OSZ",
        tz = "UTC"
    )
    acmm_assert(
        all(!is.na(parsed)),
        paste0("Column '", columnName, "' contains an invalid timestamp."),
        "acmm_schema_error"
    )
    parsed
}

acmm_parse_json <- function(value, label)
{
    acmm_require_namespace("jsonlite")
    parsed <- tryCatch(
        jsonlite::fromJSON(value, simplifyVector = FALSE),
        error = function(error)
        {
            acmm_abort(
                paste0("Invalid JSON in ", label, ": ", error$message),
                "acmm_schema_error"
            )
        }
    )
    parsed
}

acmm_validate_json_object_column <- function(values, columnName)
{
    for (index in seq_along(values))
    {
        value <- values[[index]]
        acmm_assert(
            grepl("^[[:space:]]*\\{", value) &&
                grepl("\\}[[:space:]]*$", value),
            paste0(
                "Column '", columnName, "' row ", index,
                " must contain a JSON object."
            ),
            "acmm_schema_error"
        )
        acmm_parse_json(
            value,
            paste0("column '", columnName, "' row ", index)
        )
    }
    invisible(TRUE)
}

acmm_validate_json_string_array_column <- function(values, columnName)
{
    for (index in seq_along(values))
    {
        value <- values[[index]]
        acmm_assert(
            grepl("^[[:space:]]*\\[", value) &&
                grepl("\\][[:space:]]*$", value),
            paste0(
                "Column '", columnName, "' row ", index,
                " must contain a JSON array."
            ),
            "acmm_schema_error"
        )
        parsed <- acmm_parse_json(
            value,
            paste0("column '", columnName, "' row ", index)
        )

        if (length(parsed) > 0)
        {
            valid <- vapply(parsed, is.character, logical(1))
            acmm_assert(
                all(valid) && length(unique(unlist(parsed))) == length(parsed),
                paste0(
                    "Column '", columnName, "' row ", index,
                    " must contain unique strings."
                ),
                "acmm_schema_error"
            )
        }
    }
    invisible(TRUE)
}

acmm_sha256_file <- function(path)
{
    acmm_require_namespace("digest")
    digest::digest(file = path, algo = "sha256", serialize = FALSE)
}
