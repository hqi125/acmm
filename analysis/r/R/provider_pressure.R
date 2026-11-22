load_acmm_provider_pressure_tables <- function(exportDir)
{
    exportDir <- normalizePath(exportDir, winslash = "/", mustWork = TRUE)
    required <- c(
        "lineages.csv",
        "snapshots.csv",
        "features.csv",
        "signals.csv",
        "decisions.csv",
        "pacing_events.csv"
    )
    missing <- required[!file.exists(file.path(exportDir, required))]
    acmm_assert(
        length(missing) == 0,
        paste0(
            "Provider Pressure export is missing required table(s): ",
            paste(missing, collapse = ", "),
            "."
        )
    )
    read <- function(name)
    {
        utils::read.csv(
            file.path(exportDir, name),
            stringsAsFactors = FALSE,
            check.names = FALSE,
            na.strings = c("")
        )
    }
    list(
        lineages = read("lineages.csv"),
        snapshots = read("snapshots.csv"),
        features = read("features.csv"),
        signals = read("signals.csv"),
        decisions = read("decisions.csv"),
        pacing_events = read("pacing_events.csv")
    )
}

acmm_provider_pressure_read_columns <- function(exportDir, name, columns)
{
    path <- file.path(exportDir, name)
    acmm_assert(file.exists(path), paste0("Provider Pressure export table does not exist: '", path, "'."))
    header <- tryCatch(
        utils::read.csv(
            path,
            nrows = 0,
            stringsAsFactors = FALSE,
            check.names = FALSE,
            na.strings = character(0)
        ),
        error = function(error)
        {
            acmm_abort(
                paste0("Could not read Provider Pressure table header '", path, "': ", error$message),
                "acmm_input_error"
            )
        }
    )
    available <- names(header)
    missing <- columns[!columns %in% available]
    acmm_assert(
        length(missing) == 0,
        paste0(
            "Provider Pressure table '", name, "' is missing required column(s): ",
            paste(missing, collapse = ", "),
            "."
        ),
        "acmm_schema_error"
    )
    classes <- rep("NULL", length(available))
    classes[match(columns, available)] <- "character"
    tryCatch(
        utils::read.csv(
            path,
            stringsAsFactors = FALSE,
            check.names = FALSE,
            na.strings = c(""),
            colClasses = classes
        ),
        error = function(error)
        {
            acmm_abort(
                paste0("Could not read Provider Pressure table '", path, "': ", error$message),
                "acmm_input_error"
            )
        }
    )
}

acmm_provider_pressure_table_columns <- function(exportDir, name)
{
    path <- file.path(exportDir, name)
    acmm_assert(file.exists(path), paste0("Provider Pressure export table does not exist: '", path, "'."))
    names(utils::read.csv(
        path,
        nrows = 0,
        stringsAsFactors = FALSE,
        check.names = FALSE,
        na.strings = character(0)
    ))
}

acmm_provider_pressure_summary <- function(exportDir, progress = NULL)
{
    exportDir <- normalizePath(exportDir, winslash = "/", mustWork = TRUE)
    report <- function(message)
    {
        if (!is.null(progress))
        {
            progress(message)
        }
    }
    report("Reading compact Provider Pressure snapshot/count columns...")
    snapshotColumns <- c(
        "lineage_id",
        "sequence",
        "created_at",
        "source_kind",
        "source_execution_id",
        "execution_status",
        "experiment_id",
        "package_sha256",
        "selected_run_count",
        "observations_delta",
        "reward_events_delta",
        "punishment_events_delta",
        "runtime_pressure_events_delta",
        "runtime_pressure_seconds_delta",
        "pph_pacing_events_delta",
        "pph_pacing_seconds_delta"
    )
    snapshotAvailable <- acmm_provider_pressure_table_columns(exportDir, "snapshots.csv")
    countColumns <- c("signal_count", "decision_count", "pacing_event_count")
    snapshotReadColumns <- c(
        snapshotColumns,
        countColumns[countColumns %in% snapshotAvailable]
    )
    snapshots <- acmm_provider_pressure_read_columns(
        exportDir,
        "snapshots.csv",
        snapshotReadColumns
    )
    acmm_assert(nrow(snapshots) > 0, "Provider Pressure export contains no snapshots.")

    snapshots$sequence <- as.integer(snapshots$sequence)
    snapshots$selected_run_count <- as.numeric(snapshots$selected_run_count)
    numericSnapshotColumns <- c(
        "observations_delta",
        "reward_events_delta",
        "punishment_events_delta",
        "runtime_pressure_events_delta",
        "runtime_pressure_seconds_delta",
        "pph_pacing_events_delta",
        "pph_pacing_seconds_delta"
    )
    for (name in numericSnapshotColumns)
    {
        snapshots[[name]] <- as.numeric(snapshots[[name]])
    }

    countBySnapshot <- function(fileName, valueName)
    {
        rows <- acmm_provider_pressure_read_columns(
            exportDir,
            fileName,
            c("lineage_id", "sequence")
        )
        if (nrow(rows) == 0)
        {
            return(data.frame(
                lineage_id = character(0),
                sequence = integer(0),
                value = integer(0),
                stringsAsFactors = FALSE
            ))
        }
        result <- stats::aggregate(
            rep(1L, nrow(rows)),
            by = list(
                lineage_id = rows$lineage_id,
                sequence = as.integer(rows$sequence)
            ),
            FUN = sum
        )
        names(result)[[3]] <- valueName
        result
    }

    summary <- snapshots
    countSpecs <- list(
        list(column = "signal_count", file = "signals.csv"),
        list(column = "decision_count", file = "decisions.csv"),
        list(column = "pacing_event_count", file = "pacing_events.csv")
    )
    for (spec in countSpecs)
    {
        if (spec$column %in% names(summary))
        {
            summary[[spec$column]] <- as.integer(summary[[spec$column]])
            next
        }
        counts <- countBySnapshot(spec$file, spec$column)
        if (nrow(counts) == 0)
        {
            summary[[spec$column]] <- 0L
        } else
        {
            names(counts)[names(counts) == "value"] <- spec$column
            summary <- merge(
                summary,
                counts,
                by = c("lineage_id", "sequence"),
                all.x = TRUE,
                sort = FALSE
            )
            summary[[spec$column]][is.na(summary[[spec$column]])] <- 0L
        }
    }
    summary <- summary[order(summary$lineage_id, summary$sequence), , drop = FALSE]

    report("Aggregating Provider Pressure pacing events...")
    pacing <- acmm_provider_pressure_read_columns(
        exportDir,
        "pacing_events.csv",
        c(
            "lineage_id",
            "sequence",
            "created_at",
            "source_execution_id",
            "source",
            "reason",
            "duration_seconds"
        )
    )
    pacingSummary <- data.frame(
        lineage_id = character(0),
        sequence = integer(0),
        created_at = character(0),
        source_execution_id = character(0),
        source = character(0),
        reason = character(0),
        pacing_events = integer(0),
        pacing_seconds = numeric(0),
        stringsAsFactors = FALSE
    )
    if (nrow(pacing) > 0)
    {
        pacing$sequence <- as.integer(pacing$sequence)
        pacing$duration_seconds <- as.numeric(pacing$duration_seconds)
        pacing$pacing_events <- 1L
        pacingSummary <- stats::aggregate(
            cbind(pacing_events, pacing_seconds = duration_seconds) ~
                lineage_id + sequence + created_at + source_execution_id + source + reason,
            data = pacing,
            FUN = sum
        )
        pacingSummary <- pacingSummary[
            order(pacingSummary$lineage_id, pacingSummary$sequence, pacingSummary$reason),
            ,
            drop = FALSE
        ]
    }
    rm(pacing)
    invisible(gc(FALSE))

    report("Reading learned Provider Pressure feature trajectories...")
    featureColumns <- c(
        "lineage_id",
        "sequence",
        "created_at",
        "source_kind",
        "source_execution_id",
        "feature",
        "score_before",
        "score_after",
        "score_delta",
        "observations_after",
        "rewards_after",
        "punishments_after",
        "confidence_after",
        "runtime_pressure_seconds_after",
        "pph_actuation_seconds_after"
    )
    features <- acmm_provider_pressure_read_columns(
        exportDir,
        "features.csv",
        featureColumns
    )
    featureTrajectory <- features
    featureSummary <- data.frame(
        lineage_id = character(0),
        feature = character(0),
        final_sequence = integer(0),
        final_score = numeric(0),
        maximum_score = numeric(0),
        final_confidence = numeric(0),
        final_observations = numeric(0),
        final_rewards = numeric(0),
        final_punishments = numeric(0),
        final_runtime_pressure_seconds = numeric(0),
        final_pph_actuation_seconds = numeric(0),
        stringsAsFactors = FALSE
    )
    if (nrow(features) > 0)
    {
        features$sequence <- as.integer(features$sequence)
        numericFeatureColumns <- c(
            "score_before",
            "score_after",
            "score_delta",
            "observations_after",
            "rewards_after",
            "punishments_after",
            "confidence_after",
            "runtime_pressure_seconds_after",
            "pph_actuation_seconds_after"
        )
        for (name in numericFeatureColumns)
        {
            features[[name]] <- as.numeric(features[[name]])
        }
        featureTrajectory <- features[
            order(features$lineage_id, features$feature, features$sequence),
            ,
            drop = FALSE
        ]
        keys <- unique(features[c("lineage_id", "feature")])
        rows <- vector("list", nrow(keys))
        for (index in seq_len(nrow(keys)))
        {
            subset <- features[
                features$lineage_id == keys$lineage_id[[index]] &
                    features$feature == keys$feature[[index]],
                ,
                drop = FALSE
            ]
            subset <- subset[order(subset$sequence), , drop = FALSE]
            final <- subset[nrow(subset), , drop = FALSE]
            rows[[index]] <- data.frame(
                lineage_id = keys$lineage_id[[index]],
                feature = keys$feature[[index]],
                final_sequence = final$sequence[[1]],
                final_score = final$score_after[[1]],
                maximum_score = max(subset$score_after, na.rm = TRUE),
                final_confidence = final$confidence_after[[1]],
                final_observations = final$observations_after[[1]],
                final_rewards = final$rewards_after[[1]],
                final_punishments = final$punishments_after[[1]],
                final_runtime_pressure_seconds = final$runtime_pressure_seconds_after[[1]],
                final_pph_actuation_seconds = final$pph_actuation_seconds_after[[1]],
                stringsAsFactors = FALSE
            )
        }
        featureSummary <- do.call(rbind, rows)
        featureSummary <- featureSummary[
            order(featureSummary$lineage_id, -featureSummary$maximum_score, featureSummary$feature),
            ,
            drop = FALSE
        ]
    }

    report("Provider Pressure summary tables are ready.")
    list(
        snapshot_summary = summary,
        pacing_summary = pacingSummary,
        feature_summary = featureSummary,
        feature_trajectory = featureTrajectory
    )
}

acmm_plot_provider_pressure_pacing <- function(
    analysisRoot,
    formats = c("png", "pdf")
)
{
    rows <- utils::read.csv(
        file.path(
            analysisRoot,
            "data",
            "provider_pressure",
            "provider_pressure_pacing_summary.csv"
        ),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "provider_pressure",
        "pacing_trajectory"
    )
    if (nrow(rows) == 0)
    {
        drawEmpty <- function()
        {
            graphics::plot.new()
            graphics::title(main = "Provider Pressure pacing trajectory")
            graphics::text(0.5, 0.5, "No pacing events were recorded in this export.")
        }
        acmm_render_plot(outputStem, drawEmpty, formats = formats)
        return(invisible(outputStem))
    }

    rows$sequence <- as.integer(rows$sequence)
    rows$pacing_seconds <- as.numeric(rows$pacing_seconds)
    lineages <- sort(unique(rows$lineage_id))
    reasons <- sort(unique(rows$reason))
    colours <- grDevices::hcl.colors(max(3L, length(reasons)), palette = "Dark 3")[seq_along(reasons)]

    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(
            mfrow = c(length(lineages), 1),
            mar = c(5, 6, 3.5, 11) + 0.1,
            oma = c(0, 0, 4, 0),
            xpd = NA
        )
        for (lineageIndex in seq_along(lineages))
        {
            lineageId <- lineages[[lineageIndex]]
            panel <- rows[rows$lineage_id == lineageId, , drop = FALSE]
            sequences <- seq(min(panel$sequence), max(panel$sequence))
            ymax <- max(panel$pacing_seconds, na.rm = TRUE)
            if (!is.finite(ymax) || ymax <= 0)
            {
                ymax <- 1
            }
            graphics::plot(
                NA,
                xlim = range(sequences),
                ylim = c(0, ymax * 1.08),
                xlab = "Trajectory snapshot sequence",
                ylab = "Pacing seconds in snapshot",
                main = if (length(lineages) > 1) paste0("Lineage ", lineageId) else "",
                las = 1
            )
            graphics::abline(h = pretty(c(0, ymax), n = 5), col = "grey90", lty = 3)
            for (reasonIndex in seq_along(reasons))
            {
                reason <- reasons[[reasonIndex]]
                values <- numeric(length(sequences))
                matched <- panel[panel$reason == reason, , drop = FALSE]
                if (nrow(matched) > 0)
                {
                    aggregated <- stats::aggregate(
                        matched$pacing_seconds,
                        by = list(sequence = matched$sequence),
                        FUN = sum
                    )
                    positions <- match(aggregated$sequence, sequences)
                    values[positions] <- aggregated$x
                }
                graphics::lines(
                    sequences,
                    values,
                    type = "o",
                    pch = 16,
                    cex = 0.55,
                    lwd = 1.2,
                    col = colours[[reasonIndex]]
                )
            }
            graphics::legend(
                "topright",
                inset = c(-0.34, 0),
                legend = reasons,
                col = colours,
                lty = 1,
                pch = 16,
                bty = "n",
                cex = 0.68
            )
        }
        graphics::mtext(
            "Provider Pressure pacing seconds by recorded reason",
            side = 3,
            outer = TRUE,
            line = 1.5,
            font = 2,
            cex = 1.05
        )
    }
    acmm_render_plot(
        outputStem,
        draw,
        width = 2200,
        height = max(1000, 700 * length(lineages)),
        formats = formats
    )
}

acmm_plot_provider_pressure_feature_scores <- function(
    analysisRoot,
    topN = 8L,
    formats = c("png", "pdf")
)
{
    trajectory <- utils::read.csv(
        file.path(
            analysisRoot,
            "data",
            "provider_pressure",
            "provider_pressure_feature_trajectory.csv"
        ),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    summary <- utils::read.csv(
        file.path(
            analysisRoot,
            "data",
            "provider_pressure",
            "provider_pressure_feature_summary.csv"
        ),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "provider_pressure",
        "feature_scores"
    )
    if (nrow(trajectory) == 0 || nrow(summary) == 0)
    {
        drawEmpty <- function()
        {
            graphics::plot.new()
            graphics::title(main = "Provider Pressure feature-score trajectory")
            graphics::text(0.5, 0.5, "No learned feature states were recorded in this export.")
        }
        acmm_render_plot(outputStem, drawEmpty, formats = formats)
        return(invisible(outputStem))
    }

    trajectory$sequence <- as.integer(trajectory$sequence)
    trajectory$score_after <- as.numeric(trajectory$score_after)
    summary$maximum_score <- as.numeric(summary$maximum_score)
    lineages <- sort(unique(summary$lineage_id))

    panels <- vector("list", length(lineages))
    for (index in seq_along(lineages))
    {
        lineageId <- lineages[[index]]
        ranked <- summary[summary$lineage_id == lineageId, , drop = FALSE]
        ranked <- ranked[order(-ranked$maximum_score, ranked$feature), , drop = FALSE]
        keep <- head(ranked$feature, topN)
        panels[[index]] <- trajectory[
            trajectory$lineage_id == lineageId & trajectory$feature %in% keep,
            ,
            drop = FALSE
        ]
    }

    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(
            mfrow = c(length(lineages), 1),
            mar = c(5, 6, 3.5, 12) + 0.1,
            oma = c(0, 0, 4, 0),
            xpd = NA
        )
        for (index in seq_along(lineages))
        {
            panel <- panels[[index]]
            features <- unique(panel$feature)
            colours <- grDevices::hcl.colors(max(3L, length(features)), palette = "Dark 3")[seq_along(features)]
            xRange <- range(panel$sequence)
            yMax <- max(panel$score_after, na.rm = TRUE)
            if (!is.finite(yMax) || yMax <= 0)
            {
                yMax <- 1
            }
            graphics::plot(
                NA,
                xlim = xRange,
                ylim = c(0, yMax * 1.08),
                xlab = "Trajectory snapshot sequence",
                ylab = "Learned feature score",
                main = if (length(lineages) > 1) paste0("Lineage ", lineages[[index]]) else "",
                las = 1
            )
            graphics::abline(h = pretty(c(0, yMax), n = 5), col = "grey90", lty = 3)
            for (featureIndex in seq_along(features))
            {
                feature <- features[[featureIndex]]
                values <- panel[panel$feature == feature, , drop = FALSE]
                values <- values[order(values$sequence), , drop = FALSE]
                graphics::lines(
                    values$sequence,
                    values$score_after,
                    type = "o",
                    pch = 16,
                    cex = 0.5,
                    lwd = 1.2,
                    col = colours[[featureIndex]]
                )
            }
            graphics::legend(
                "topright",
                inset = c(-0.38, 0),
                legend = features,
                col = colours,
                lty = 1,
                pch = 16,
                bty = "n",
                cex = 0.62
            )
        }
        graphics::mtext(
            paste0("Top ", topN, " Provider Pressure feature scores over trajectory"),
            side = 3,
            outer = TRUE,
            line = 1.5,
            font = 2,
            cex = 1.05
        )
    }
    acmm_render_plot(
        outputStem,
        draw,
        width = 2300,
        height = max(1050, 750 * length(lineages)),
        formats = formats
    )
}
