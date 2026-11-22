acmm_render_plot_pair <- function(outputStem, draw, width = 1600, height = 1000)
{
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

    renderDevice(function()
    {
        grDevices::png(
            filename = paste0(outputStem, ".png"),
            width = width,
            height = height,
            res = 160
        )
    })
    renderDevice(function()
    {
        grDevices::pdf(
            file = paste0(outputStem, ".pdf"),
            width = width / 160,
            height = height / 160,
            onefile = FALSE
        )
    })
    invisible(outputStem)
}

acmm_plot_run_duration <- function(exportData, analysisRoot, populationPolicy = NULL)
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
        graphics::par(mar = c(9, 5, 4, 2) + 0.1)
        graphics::boxplot(
            durations ~ runs$subject_id,
            las = 2,
            ylab = "Duration (seconds)",
            xlab = "",
            main = "Selected run duration by subject"
        )
    }
    acmm_render_plot_pair(outputStem, draw)
}

acmm_plot_requirement_compliance <- function(
    analysisRoot,
    estimand = "scenario",
    includeIntervals = TRUE
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
    requirements <- requirements[order(requirements$dimension, requirements$requirement_id), , drop = FALSE]
    subjects <- sort(unique(rows$subject_id))
    offsets <- seq(-0.24, 0.24, length.out = max(1, length(subjects)))
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
        graphics::par(mar = c(5, 15, 4, 2) + 0.1)
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
            las = 2,
            cex.axis = 0.72
        )
        graphics::abline(v = seq(0, 1, by = 0.25), lty = 3)
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
                            y
                        )
                    }
                }
                graphics::points(point$p_hat[[1]], y, pch = 14 + subjectIndex)
            }
        }
        graphics::legend(
            "bottomright",
            legend = subjects,
            pch = 14 + seq_along(subjects),
            bty = "n",
            cex = 0.82
        )
    }
    acmm_render_plot_pair(outputStem, draw, height = 1200)
}

acmm_plot_dimension_scores <- function(
    analysisRoot,
    estimand = "opportunity",
    aggregationMethod = "arithmetic",
    includeIntervals = TRUE
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

    dimensions <- sort(unique(rows$dimension))
    subjects <- sort(unique(rows$subject_id))
    offsets <- seq(-0.24, 0.24, length.out = max(1, length(subjects)))
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
        graphics::par(mar = c(5, 8, 4, 2) + 0.1)
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
        graphics::axis(2, at = yBase, labels = dimensions, las = 2)
        graphics::abline(v = seq(0, 1, by = 0.25), lty = 3)
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
                            y
                        )
                    }
                }
                graphics::points(point$s_d[[1]], y, pch = 14 + subjectIndex)
            }
        }
        graphics::legend(
            "bottomright",
            legend = subjects,
            pch = 14 + seq_along(subjects),
            bty = "n",
            cex = 0.82
        )
    }
    acmm_render_plot_pair(outputStem, draw)
}

acmm_plot_availability <- function(analysisRoot)
{
    summary <- utils::read.csv(
        file.path(analysisRoot, "data", "availability", "availability_summary.csv"),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    rows <- summary[summary$scope == "subject", , drop = FALSE]
    acmm_assert(nrow(rows) > 0, "Availability plot requires subject-level summary rows.")
    values <- rbind(
        rows$first_attempt_availability_fraction,
        rows$availability_fraction
    )
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
        graphics::par(mar = c(9, 5, 4, 2) + 0.1)
        graphics::barplot(
            values,
            beside = TRUE,
            names.arg = rows$subject_id,
            las = 2,
            ylim = c(0, 1),
            ylab = "Availability fraction",
            main = "Acquisition availability by subject"
        )
        graphics::legend(
            "bottomleft",
            legend = c("First attempt", "Final after retry"),
            fill = c("grey70", "grey35"),
            bty = "n"
        )
    }
    acmm_render_plot_pair(outputStem, draw)
}

acmm_plot_trajectory_run_metrics <- function(analysisRoot, metric = "tool_calls")
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
        graphics::par(mar = c(9, 5, 4, 2) + 0.1)
        graphics::boxplot(
            values ~ rows$subject_id,
            las = 2,
            ylab = definition$label,
            xlab = "",
            main = definition$label
        )
    }
    acmm_render_plot_pair(outputStem, draw)
}

acmm_plot_trajectory_tool_usage <- function(analysisRoot, topN = 12L)
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
        acmm_render_plot_pair(outputStem, drawEmpty)
        return(invisible(outputStem))
    }
    totals <- stats::aggregate(calls ~ tool_name, data = usage, FUN = sum)
    totals <- totals[order(-totals$calls, totals$tool_name), , drop = FALSE]
    selectedTools <- head(totals$tool_name, topN)
    subjects <- sort(unique(usage$subject_id))
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
        graphics::par(mar = c(5, 16, 4, 2) + 0.1)
        graphics::barplot(
            matrixValues,
            beside = TRUE,
            horiz = TRUE,
            names.arg = selectedTools,
            las = 1,
            xlab = "Tool calls",
            main = paste0("Top ", length(selectedTools), " tools by call volume")
        )
        graphics::legend(
            "bottomright",
            legend = subjects,
            fill = seq_along(subjects),
            bty = "n",
            cex = 0.8
        )
    }
    acmm_render_plot_pair(outputStem, draw, height = 1200)
}
