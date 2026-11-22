acmm_normalize_plot_formats <- function(formats)
{
    formats <- unique(tolower(trimws(as.character(formats))))
    formats <- formats[nzchar(formats)]
    acmm_assert(length(formats) > 0, "At least one plot format is required.")
    supported <- c("png", "pdf")
    unsupported <- setdiff(formats, supported)
    acmm_assert(
        length(unsupported) == 0,
        paste0("Unsupported plot format(s): ", paste(unsupported, collapse = ", "), ".")
    )
    formats
}

acmm_render_plot <- function(
    outputStem,
    draw,
    width = 1600,
    height = 1000,
    formats = c("png", "pdf")
)
{
    formats <- acmm_normalize_plot_formats(formats)
    outputDir <- dirname(outputStem)
    if (!dir.exists(outputDir))
    {
        dir.create(outputDir, recursive = TRUE, showWarnings = FALSE)
    }

    renderDevice <- function(openDevice)
    {
        openDevice()
        tryCatch(
            draw(),
            finally = grDevices::dev.off()
        )
    }

    if ("png" %in% formats)
    {
        renderDevice(function()
        {
            grDevices::png(
                filename = paste0(outputStem, ".png"),
                width = width,
                height = height,
                res = 160
            )
        })
    }

    if ("pdf" %in% formats)
    {
        renderDevice(function()
        {
            grDevices::pdf(
                file = paste0(outputStem, ".pdf"),
                width = width / 160,
                height = height / 160,
                onefile = FALSE
            )
        })
    }
    invisible(outputStem)
}

acmm_plot_subject_labels <- function(subjects)
{
    subjects <- as.character(subjects)
    if (length(subjects) <= 1)
    {
        return(subjects)
    }

    tokens <- strsplit(subjects, "_", fixed = TRUE)
    minimumLength <- min(vapply(tokens, length, integer(1)))
    commonSuffixLength <- 0L
    if (minimumLength > 1L)
    {
        for (offset in seq_len(minimumLength - 1L))
        {
            suffixTokens <- vapply(
                tokens,
                function(value)
                {
                    value[[length(value) - offset + 1L]]
                },
                character(1)
            )
            if (length(unique(suffixTokens)) != 1L)
            {
                break
            }
            commonSuffixLength <- offset
        }
    }

    if (commonSuffixLength == 0L)
    {
        return(subjects)
    }

    labels <- vapply(
        tokens,
        function(value)
        {
            remaining <- length(value) - commonSuffixLength
            paste(value[seq_len(remaining)], collapse = "_")
        },
        character(1)
    )
    if (any(!nzchar(labels)) || anyDuplicated(labels))
    {
        return(subjects)
    }
    labels
}

acmm_subject_palette <- function(subjects)
{
    count <- length(subjects)
    acmm_assert(count >= 1L, "Subject palette requires at least one subject.")
    grDevices::hcl.colors(max(3L, count), palette = "Dark 3")[seq_len(count)]
}

acmm_subject_shapes <- function(subjects)
{
    shapes <- c(15, 17, 16, 18, 8, 3, 4, 1)
    rep(shapes, length.out = length(subjects))
}

acmm_order_dimensions <- function(values)
{
    values <- unique(as.character(values))
    canonical <- c("goal", "obligation", "boundary", "procedure", "fidelity", "recovery")
    c(canonical[canonical %in% values], sort(setdiff(values, canonical)))
}

acmm_dimension_display_labels <- function(values)
{
    values <- as.character(values)
    paste0(toupper(substr(values, 1, 1)), substr(values, 2, nchar(values)))
}

acmm_bottom_subject_legend <- function(subjects, colours, shapes = NULL, fill = NULL)
{
    arguments <- list(
        x = "bottom",
        inset = c(0, -0.19),
        legend = acmm_plot_subject_labels(subjects),
        bty = "n",
        horiz = TRUE,
        xpd = NA,
        cex = 0.82
    )
    if (!is.null(shapes))
    {
        arguments$pch <- shapes
        arguments$col <- colours
    }
    if (!is.null(fill))
    {
        arguments$fill <- fill
    }
    do.call(graphics::legend, arguments)
}

acmm_plot_run_duration <- function(
    exportData,
    analysisRoot,
    populationPolicy = NULL,
    formats = c("png", "pdf")
)
{
    population <- build_acmm_population(
        exportData,
        populationPolicy = populationPolicy
    )
    runs <- population$runs
    acmm_assert(nrow(runs) > 0, "Run-duration plot requires at least one selected run.")
    durations <- as.numeric(difftime(runs$ended_at, runs$started_at, units = "secs"))
    acmm_assert(
        all(is.finite(durations) & durations >= 0),
        "Run-duration plot encountered an invalid selected-run duration."
    )
    subjects <- sort(unique(runs$subject_id))
    subjectFactor <- factor(runs$subject_id, levels = subjects)
    colours <- acmm_subject_palette(subjects)
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "operations",
        "run_duration"
    )
    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(mar = c(6, 5, 4, 2) + 0.1)
        graphics::boxplot(
            durations ~ subjectFactor,
            names = acmm_plot_subject_labels(subjects),
            col = colours,
            las = 1,
            ylab = "Duration (seconds)",
            xlab = "",
            main = "Selected run duration by subject"
        )
    }
    acmm_render_plot(outputStem, draw, formats = formats)
}

acmm_plot_requirement_compliance <- function(
    analysisRoot,
    estimand = "scenario",
    includeIntervals = TRUE,
    formats = c("png", "pdf")
)
{
    dataDir <- file.path(analysisRoot, "data", "compliance")
    estimates <- utils::read.csv(
        file.path(dataDir, "requirement_estimates.csv"),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    rows <- estimates[estimates$estimand == estimand, , drop = FALSE]
    acmm_assert(nrow(rows) > 0, "No requirement estimates match the requested plot estimand.")
    intervals <- NULL
    if (includeIntervals)
    {
        intervals <- utils::read.csv(
            file.path(dataDir, "requirement_intervals.csv"),
            stringsAsFactors = FALSE,
            check.names = FALSE
        )
        intervals <- intervals[intervals$estimand == estimand, , drop = FALSE]
    }

    requirements <- unique(rows[c("requirement_id", "dimension")])
    dimensionOrder <- acmm_order_dimensions(requirements$dimension)
    requirements$dimension_order <- match(requirements$dimension, dimensionOrder)
    requirements <- requirements[
        order(requirements$dimension_order, requirements$requirement_id),
        c("requirement_id", "dimension"),
        drop = FALSE
    ]
    requirements <- requirements[rev(seq_len(nrow(requirements))), , drop = FALSE]
    subjects <- sort(unique(rows$subject_id))
    offsets <- seq(-0.24, 0.24, length.out = max(1, length(subjects)))
    colours <- acmm_subject_palette(subjects)
    shapes <- acmm_subject_shapes(subjects)
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "compliance",
        "requirement_compliance"
    )

    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(mar = c(8, 17, 4, 2) + 0.1)
        yBase <- seq_len(nrow(requirements))
        graphics::plot(
            NA,
            xlim = c(0, 1),
            ylim = c(0.5, nrow(requirements) + 0.5),
            xlab = "Compliance estimate",
            ylab = "",
            yaxt = "n",
            main = paste0("Requirement compliance (", estimand, " estimand)")
        )
        graphics::axis(
            2,
            at = yBase,
            labels = paste(requirements$dimension, requirements$requirement_id, sep = ": "),
            las = 1,
            cex.axis = 0.72
        )
        graphics::abline(v = seq(0, 1, by = 0.25), lty = 3, col = "grey80")
        for (subjectIndex in seq_along(subjects))
        {
            subjectId <- subjects[[subjectIndex]]
            for (requirementIndex in seq_len(nrow(requirements)))
            {
                requirementId <- requirements$requirement_id[[requirementIndex]]
                point <- rows[
                    rows$subject_id == subjectId & rows$requirement_id == requirementId,
                    ,
                    drop = FALSE
                ]
                if (nrow(point) != 1 || is.na(point$p_hat[[1]]))
                {
                    next
                }
                y <- yBase[[requirementIndex]] + offsets[[subjectIndex]]
                if (!is.null(intervals))
                {
                    interval <- intervals[
                        intervals$subject_id == subjectId &
                            intervals$requirement_id == requirementId,
                        ,
                        drop = FALSE
                    ]
                    if (
                        nrow(interval) == 1 &&
                        !is.na(interval$p_hat_lower[[1]]) &&
                        !is.na(interval$p_hat_upper[[1]])
                    )
                    {
                        graphics::segments(
                            interval$p_hat_lower[[1]],
                            y,
                            interval$p_hat_upper[[1]],
                            y,
                            col = grDevices::adjustcolor(colours[[subjectIndex]], alpha.f = 0.65),
                            lwd = 1.2
                        )
                    }
                }
                graphics::points(
                    point$p_hat[[1]],
                    y,
                    pch = shapes[[subjectIndex]],
                    col = colours[[subjectIndex]]
                )
            }
        }
        acmm_bottom_subject_legend(subjects, colours, shapes = shapes)
    }
    acmm_render_plot(outputStem, draw, width = 1800, height = 1250, formats = formats)
}

acmm_plot_dimension_scores <- function(
    analysisRoot,
    estimand = "opportunity",
    aggregationMethod = "arithmetic",
    includeIntervals = TRUE,
    formats = c("png", "pdf")
)
{
    dataDir <- file.path(analysisRoot, "data", "compliance")
    scores <- utils::read.csv(
        file.path(dataDir, "dimension_scores.csv"),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    rows <- scores[
        scores$estimand == estimand & scores$aggregation_method == aggregationMethod,
        ,
        drop = FALSE
    ]
    acmm_assert(nrow(rows) > 0, "No dimension scores match the requested plot parameters.")
    intervals <- NULL
    if (includeIntervals)
    {
        intervals <- utils::read.csv(
            file.path(dataDir, "dimension_intervals.csv"),
            stringsAsFactors = FALSE,
            check.names = FALSE
        )
        intervals <- intervals[
            intervals$estimand == estimand &
                intervals$aggregation_method == aggregationMethod,
            ,
            drop = FALSE
        ]
    }

    dimensions <- rev(acmm_order_dimensions(rows$dimension))
    subjects <- sort(unique(rows$subject_id))
    offsets <- seq(-0.24, 0.24, length.out = max(1, length(subjects)))
    colours <- acmm_subject_palette(subjects)
    shapes <- acmm_subject_shapes(subjects)
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "compliance",
        "dimension_scores"
    )

    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(mar = c(8, 8, 4, 2) + 0.1)
        yBase <- seq_along(dimensions)
        graphics::plot(
            NA,
            xlim = c(0, 1),
            ylim = c(0.5, length(dimensions) + 0.5),
            xlab = "Dimension score",
            ylab = "",
            yaxt = "n",
            main = paste0(
                "GOBPFR dimension scores (",
                estimand,
                ", ",
                aggregationMethod,
                ")"
            )
        )
        graphics::axis(
            2,
            at = yBase,
            labels = acmm_dimension_display_labels(dimensions),
            las = 1
        )
        graphics::abline(v = seq(0, 1, by = 0.25), lty = 3, col = "grey80")
        for (subjectIndex in seq_along(subjects))
        {
            subjectId <- subjects[[subjectIndex]]
            for (dimensionIndex in seq_along(dimensions))
            {
                dimension <- dimensions[[dimensionIndex]]
                point <- rows[
                    rows$subject_id == subjectId & rows$dimension == dimension,
                    ,
                    drop = FALSE
                ]
                if (nrow(point) != 1 || is.na(point$s_d[[1]]))
                {
                    next
                }
                y <- yBase[[dimensionIndex]] + offsets[[subjectIndex]]
                if (!is.null(intervals))
                {
                    interval <- intervals[
                        intervals$subject_id == subjectId & intervals$dimension == dimension,
                        ,
                        drop = FALSE
                    ]
                    if (
                        nrow(interval) == 1 &&
                        !is.na(interval$s_d_lower[[1]]) &&
                        !is.na(interval$s_d_upper[[1]])
                    )
                    {
                        graphics::segments(
                            interval$s_d_lower[[1]],
                            y,
                            interval$s_d_upper[[1]],
                            y,
                            col = grDevices::adjustcolor(colours[[subjectIndex]], alpha.f = 0.65),
                            lwd = 1.2
                        )
                    }
                }
                graphics::points(
                    point$s_d[[1]],
                    y,
                    pch = shapes[[subjectIndex]],
                    col = colours[[subjectIndex]]
                )
            }
        }
        acmm_bottom_subject_legend(subjects, colours, shapes = shapes)
    }
    acmm_render_plot(outputStem, draw, width = 1800, height = 1050, formats = formats)
}

acmm_plot_dimension_profile <- function(
    analysisRoot,
    estimand = "opportunity",
    aggregationMethod = "arithmetic",
    formats = c("png", "pdf")
)
{
    scores <- utils::read.csv(
        file.path(analysisRoot, "data", "compliance", "dimension_scores.csv"),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    rows <- scores[
        scores$estimand == estimand & scores$aggregation_method == aggregationMethod,
        ,
        drop = FALSE
    ]
    acmm_assert(nrow(rows) > 0, "No dimension scores match the requested profile parameters.")
    dimensions <- acmm_order_dimensions(rows$dimension)
    subjects <- sort(unique(rows$subject_id))
    colours <- acmm_subject_palette(subjects)
    matrixValues <- matrix(
        NA_real_,
        nrow = length(subjects),
        ncol = length(dimensions),
        dimnames = list(subjects, dimensions)
    )
    for (subjectId in subjects)
    {
        for (dimension in dimensions)
        {
            row <- rows[
                rows$subject_id == subjectId & rows$dimension == dimension,
                ,
                drop = FALSE
            ]
            if (nrow(row) == 1)
            {
                matrixValues[subjectId, dimension] <- row$s_d[[1]]
            }
        }
    }
    acmm_assert(
        all(is.finite(matrixValues)),
        "GOBPFR profile requires one finite score for every subject/dimension combination."
    )
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "compliance",
        "dimension_profile"
    )
    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(mar = c(8, 5, 4, 2) + 0.1)
        graphics::barplot(
            matrixValues,
            beside = TRUE,
            col = colours,
            names.arg = acmm_dimension_display_labels(dimensions),
            las = 1,
            ylim = c(0, 1),
            ylab = "Dimension score",
            main = paste0(
                "GOBPFR compliance profile (",
                estimand,
                ", ",
                aggregationMethod,
                ")"
            )
        )
        graphics::abline(h = seq(0, 1, by = 0.25), lty = 3, col = "grey80")
        acmm_bottom_subject_legend(subjects, colours, fill = colours)
    }
    acmm_render_plot(outputStem, draw, width = 1800, height = 1050, formats = formats)
}

acmm_plot_availability <- function(analysisRoot, formats = c("png", "pdf"))
{
    summary <- utils::read.csv(
        file.path(analysisRoot, "data", "availability", "availability_summary.csv"),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    rows <- summary[summary$scope == "subject", , drop = FALSE]
    acmm_assert(nrow(rows) > 0, "Availability plot requires subject-level summary rows.")
    rows <- rows[order(rows$subject_id), , drop = FALSE]
    values <- rbind(
        rows$first_attempt_availability_fraction,
        rows$availability_fraction
    )
    seriesColours <- c("grey70", "grey35")
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "availability",
        "availability"
    )
    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(mar = c(6, 5, 4, 2) + 0.1)
        graphics::barplot(
            values,
            beside = TRUE,
            col = seriesColours,
            names.arg = acmm_plot_subject_labels(rows$subject_id),
            las = 1,
            ylim = c(0, 1),
            ylab = "Availability fraction",
            main = "Acquisition availability by subject"
        )
        graphics::legend(
            "bottomleft",
            legend = c("First attempt", "Final after retry"),
            fill = seriesColours,
            bty = "n"
        )
    }
    acmm_render_plot(outputStem, draw, formats = formats)
}

acmm_plot_trajectory_run_metrics <- function(
    analysisRoot,
    metric = "tool_calls",
    formats = c("png", "pdf")
)
{
    rows <- utils::read.csv(
        file.path(analysisRoot, "data", "trajectory", "trajectory_run_metrics.csv"),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    metricMap <- list(
        tool_calls = list(column = "tool_calls", label = "Tool calls per selected run"),
        distinct_tools = list(column = "distinct_tools", label = "Distinct tools per selected run"),
        error_rate = list(column = "error_rate", label = "Tool-result error rate per selected run"),
        repeated_calls = list(column = "repeated_calls", label = "Repeated tool calls per selected run"),
        unmatched_calls = list(column = "unmatched_total", label = "Unmatched tool events per selected run")
    )
    definition <- metricMap[[metric]]
    acmm_assert(!is.null(definition), paste0("Unsupported trajectory run metric '", metric, "'."))
    if (identical(metric, "unmatched_calls"))
    {
        rows$unmatched_total <- rows$unmatched_calls + rows$unmatched_results
    }
    values <- rows[[definition$column]]
    subjects <- sort(unique(rows$subject_id))
    subjectFactor <- factor(rows$subject_id, levels = subjects)
    colours <- acmm_subject_palette(subjects)
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "trajectory",
        "trajectory_run_metrics"
    )
    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(mar = c(6, 5, 4, 2) + 0.1)
        graphics::boxplot(
            values ~ subjectFactor,
            names = acmm_plot_subject_labels(subjects),
            col = colours,
            las = 1,
            ylab = definition$label,
            xlab = "",
            main = definition$label
        )
    }
    acmm_render_plot(outputStem, draw, formats = formats)
}

acmm_plot_trajectory_tool_usage <- function(
    analysisRoot,
    topN = 12L,
    formats = c("png", "pdf")
)
{
    usage <- utils::read.csv(
        file.path(analysisRoot, "data", "trajectory", "trajectory_tool_usage.csv"),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "trajectory",
        "trajectory_tool_usage"
    )
    if (nrow(usage) == 0)
    {
        drawEmpty <- function()
        {
            graphics::plot.new()
            graphics::title(main = "Tool usage by subject")
            graphics::text(0.5, 0.5, "No tool calls observed in the selected population.")
        }
        acmm_render_plot(outputStem, drawEmpty, formats = formats)
        return(invisible(outputStem))
    }
    totals <- stats::aggregate(calls ~ tool_name, data = usage, FUN = sum)
    totals <- totals[order(-totals$calls, totals$tool_name), , drop = FALSE]
    selectedTools <- head(totals$tool_name, topN)
    subjects <- sort(unique(usage$subject_id))
    colours <- acmm_subject_palette(subjects)
    matrixValues <- matrix(
        0,
        nrow = length(subjects),
        ncol = length(selectedTools),
        dimnames = list(subjects, selectedTools)
    )
    for (subjectId in subjects)
    {
        for (toolName in selectedTools)
        {
            row <- usage[
                usage$subject_id == subjectId & usage$tool_name == toolName,
                ,
                drop = FALSE
            ]
            if (nrow(row) == 1)
            {
                matrixValues[subjectId, toolName] <- row$calls[[1]]
            }
        }
    }
    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(mar = c(8, 20, 4, 2) + 0.1)
        graphics::barplot(
            matrixValues,
            beside = TRUE,
            horiz = TRUE,
            col = colours,
            names.arg = selectedTools,
            cex.names = 0.82,
            las = 1,
            xlab = "Tool calls",
            main = paste0("Top ", length(selectedTools), " tools by call volume")
        )
        acmm_bottom_subject_legend(subjects, colours, fill = colours)
    }
    acmm_render_plot(outputStem, draw, width = 1900, height = 1250, formats = formats)
}

acmm_plot_trajectory_error_rate <- function(
    analysisRoot,
    formats = c("png", "pdf")
)
{
    rows <- utils::read.csv(
        file.path(analysisRoot, "data", "trajectory", "trajectory_run_metrics.csv"),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    acmm_assert(nrow(rows) > 0, "Trajectory error-rate plot requires selected runs.")
    rows <- rows[!is.na(rows$error_rate), , drop = FALSE]
    acmm_assert(nrow(rows) > 0, "Trajectory error-rate plot requires tool-result observations.")
    subjects <- sort(unique(rows$subject_id))
    subjectFactor <- factor(rows$subject_id, levels = subjects)
    colours <- acmm_subject_palette(subjects)
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "trajectory",
        "trajectory_error_rate"
    )
    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(mar = c(6, 6, 4, 2) + 0.1)
        graphics::boxplot(
            rows$error_rate ~ subjectFactor,
            names = acmm_plot_subject_labels(subjects),
            col = colours,
            las = 1,
            ylim = c(0, 1),
            yaxt = "n",
            ylab = "Tool-result error rate per selected run",
            xlab = "",
            main = "Tool-result error rate by subject"
        )
        ticks <- seq(0, 1, by = 0.25)
        graphics::axis(2, at = ticks, labels = paste0(round(ticks * 100), "%"), las = 1)
        graphics::box()
    }
    acmm_render_plot(outputStem, draw, formats = formats)
}

acmm_plot_trajectory_tool_composition <- function(
    analysisRoot,
    topN = 12L,
    formats = c("png", "pdf")
)
{
    usage <- utils::read.csv(
        file.path(analysisRoot, "data", "trajectory", "trajectory_tool_usage.csv"),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    usage <- usage[usage$calls > 0, , drop = FALSE]
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "trajectory",
        "trajectory_tool_composition"
    )
    if (nrow(usage) == 0)
    {
        drawEmpty <- function()
        {
            graphics::plot.new()
            graphics::title(main = "Tool composition by subject")
            graphics::text(0.5, 0.5, "No tool calls observed in the selected population.")
        }
        acmm_render_plot(outputStem, drawEmpty, formats = formats)
        return(invisible(outputStem))
    }

    subjects <- sort(unique(usage$subject_id))
    colours <- acmm_subject_palette(subjects)
    panels <- vector("list", length(subjects))
    maxShare <- 0
    for (index in seq_along(subjects))
    {
        subjectId <- subjects[[index]]
        subjectRows <- usage[usage$subject_id == subjectId, , drop = FALSE]
        subjectRows <- subjectRows[order(-subjectRows$calls, subjectRows$tool_name), , drop = FALSE]
        subjectRows <- head(subjectRows, topN)
        totalCalls <- sum(usage$calls[usage$subject_id == subjectId])
        subjectRows$call_share <- subjectRows$calls / totalCalls
        subjectRows <- subjectRows[order(subjectRows$call_share, subjectRows$tool_name), , drop = FALSE]
        panels[[index]] <- subjectRows
        if (nrow(subjectRows) > 0)
        {
            maxShare <- max(maxShare, subjectRows$call_share)
        }
    }
    axisMax <- min(1, maxShare * 1.08)
    if (axisMax <= 0)
    {
        axisMax <- 1
    }
    ticks <- pretty(c(0, axisMax), n = 5)
    ticks <- ticks[ticks >= 0 & ticks <= axisMax]

    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(
            mfrow = c(length(subjects), 1),
            mar = c(3.6, 22, 3.2, 2) + 0.1,
            oma = c(3, 0, 5, 0)
        )
        for (index in seq_along(subjects))
        {
            subjectRows <- panels[[index]]
            graphics::barplot(
                subjectRows$call_share,
                horiz = TRUE,
                col = colours[[index]],
                names.arg = subjectRows$tool_name,
                cex.names = 0.78,
                las = 1,
                xlim = c(0, axisMax),
                xaxt = "n",
                xlab = "",
                main = acmm_plot_subject_labels(subjects)[[index]]
            )
            graphics::axis(
                1,
                at = ticks,
                labels = paste0(round(ticks * 100), "%")
            )
            graphics::box()
        }
        graphics::mtext(
            paste0("Top ", topN, " raw tool names by within-subject call share"),
            side = 3,
            outer = TRUE,
            line = 2.3,
            font = 2,
            cex = 1.05
        )
        graphics::mtext(
            "Raw tool names are not canonicalized across subjects.",
            side = 3,
            outer = TRUE,
            line = 0.8,
            cex = 0.82
        )
        graphics::mtext(
            "Share of subject tool calls",
            side = 1,
            outer = TRUE,
            line = 1.2
        )
    }
    plotHeight <- max(1400, 650 * length(subjects))
    acmm_render_plot(
        outputStem,
        draw,
        width = 1900,
        height = plotHeight,
        formats = formats
    )
}

