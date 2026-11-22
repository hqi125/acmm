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

    compactPacingPath <- file.path(exportDir, "pacing_summary.csv")
    if (file.exists(compactPacingPath))
    {
        report("Reading compact Provider Pressure pacing summary...")
        pacingSummary <- acmm_provider_pressure_read_columns(
            exportDir,
            "pacing_summary.csv",
            c(
                "lineage_id",
                "sequence",
                "created_at",
                "source_execution_id",
                "source",
                "reason",
                "pacing_events",
                "pacing_seconds"
            )
        )
        if (nrow(pacingSummary) > 0)
        {
            pacingSummary$sequence <- as.integer(pacingSummary$sequence)
            pacingSummary$pacing_events <- as.integer(pacingSummary$pacing_events)
            pacingSummary$pacing_seconds <- as.numeric(pacingSummary$pacing_seconds)
            pacingSummary <- pacingSummary[
                order(pacingSummary$lineage_id, pacingSummary$sequence, pacingSummary$reason),
                ,
                drop = FALSE
            ]
        }
    } else
    {
        report("Aggregating legacy Provider Pressure pacing events...")
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
    }

    compactAdmissionPath <- file.path(exportDir, "admission_summary.csv")
    if (file.exists(compactAdmissionPath))
    {
        report("Reading compact Provider Pressure admission summary...")
        admissionSummary <- acmm_provider_pressure_read_columns(
            exportDir,
            "admission_summary.csv",
            c(
                "lineage_id",
                "sequence",
                "created_at",
                "source_execution_id",
                "run_index",
                "subject_id",
                "scenario_id",
                "action_count",
                "total_admission_wait_seconds",
                "mean_admission_wait_seconds",
                "max_admission_wait_seconds",
                "event_pph_wait_seconds",
                "app_suspension_seconds",
                "baseline_spacing_wait_seconds",
                "provider_wait_seconds",
                "underprediction_seconds"
            )
        )
    } else
    {
        report("Aggregating legacy Provider Pressure admission decisions...")
        decisions <- acmm_provider_pressure_read_columns(
            exportDir,
            "decisions.csv",
            c(
                "lineage_id",
                "sequence",
                "created_at",
                "source_execution_id",
                "decision_kind",
                "run_index",
                "subject_id",
                "scenario_id",
                "total_admission_wait_seconds",
                "event_pph_wait_seconds",
                "app_suspension_seconds",
                "baseline_spacing_wait_seconds",
                "provider_wait_seconds",
                "underprediction_seconds"
            )
        )
        decisions <- decisions[decisions$decision_kind == "action", , drop = FALSE]
        admissionSummary <- data.frame(
            lineage_id = character(0),
            sequence = integer(0),
            created_at = character(0),
            source_execution_id = character(0),
            run_index = character(0),
            subject_id = character(0),
            scenario_id = character(0),
            action_count = integer(0),
            total_admission_wait_seconds = numeric(0),
            mean_admission_wait_seconds = numeric(0),
            max_admission_wait_seconds = numeric(0),
            event_pph_wait_seconds = numeric(0),
            app_suspension_seconds = numeric(0),
            baseline_spacing_wait_seconds = numeric(0),
            provider_wait_seconds = numeric(0),
            underprediction_seconds = numeric(0),
            stringsAsFactors = FALSE
        )
        if (nrow(decisions) > 0)
        {
            numericNames <- c(
                "total_admission_wait_seconds",
                "event_pph_wait_seconds",
                "app_suspension_seconds",
                "baseline_spacing_wait_seconds",
                "provider_wait_seconds",
                "underprediction_seconds"
            )
            for (name in numericNames)
            {
                decisions[[name]] <- as.numeric(decisions[[name]])
                decisions[[name]][is.na(decisions[[name]])] <- 0
            }
            grouping <- interaction(
                decisions$lineage_id,
                decisions$sequence,
                decisions$source_execution_id,
                decisions$run_index,
                decisions$subject_id,
                decisions$scenario_id,
                drop = TRUE,
                lex.order = TRUE
            )
            groups <- split(seq_len(nrow(decisions)), grouping)
            rows <- lapply(groups, function(indices)
            {
                group <- decisions[indices, , drop = FALSE]
                total <- sum(group$total_admission_wait_seconds)
                data.frame(
                    lineage_id = group$lineage_id[[1]],
                    sequence = as.integer(group$sequence[[1]]),
                    created_at = group$created_at[[1]],
                    source_execution_id = group$source_execution_id[[1]],
                    run_index = group$run_index[[1]],
                    subject_id = group$subject_id[[1]],
                    scenario_id = group$scenario_id[[1]],
                    action_count = nrow(group),
                    total_admission_wait_seconds = total,
                    mean_admission_wait_seconds = total / nrow(group),
                    max_admission_wait_seconds = max(group$total_admission_wait_seconds),
                    event_pph_wait_seconds = sum(group$event_pph_wait_seconds),
                    app_suspension_seconds = sum(group$app_suspension_seconds),
                    baseline_spacing_wait_seconds = sum(group$baseline_spacing_wait_seconds),
                    provider_wait_seconds = sum(group$provider_wait_seconds),
                    underprediction_seconds = sum(group$underprediction_seconds),
                    stringsAsFactors = FALSE
                )
            })
            admissionSummary <- do.call(rbind, rows)
        }
        rm(decisions)
        invisible(gc(FALSE))
    }
    if (nrow(admissionSummary) > 0)
    {
        admissionSummary$sequence <- as.integer(admissionSummary$sequence)
        admissionSummary$action_count <- as.integer(admissionSummary$action_count)
        numericAdmissionColumns <- c(
            "total_admission_wait_seconds",
            "mean_admission_wait_seconds",
            "max_admission_wait_seconds",
            "event_pph_wait_seconds",
            "app_suspension_seconds",
            "baseline_spacing_wait_seconds",
            "provider_wait_seconds",
            "underprediction_seconds"
        )
        for (name in numericAdmissionColumns)
        {
            admissionSummary[[name]] <- as.numeric(admissionSummary[[name]])
        }
        admissionSummary <- admissionSummary[
            order(
                admissionSummary$lineage_id,
                admissionSummary$sequence,
                admissionSummary$subject_id,
                admissionSummary$run_index
            ),
            ,
            drop = FALSE
        ]
    }

    utilizationSummary <- data.frame(
        lineage_id = character(0),
        sequence = integer(0),
        created_at = character(0),
        source_execution_id = character(0),
        run_index = character(0),
        subject_id = character(0),
        scenario_id = character(0),
        action_count = integer(0),
        utilization_sample_count = integer(0),
        first_decision_index = integer(0),
        last_decision_index = integer(0),
        mean_content_requests_per_hour_utilization = numeric(0),
        max_content_requests_per_hour_utilization = numeric(0),
        mean_content_requests_per_minute_utilization = numeric(0),
        max_content_requests_per_minute_utilization = numeric(0),
        stringsAsFactors = FALSE
    )
    compactUtilizationPath <- file.path(exportDir, "utilization_summary.csv")
    if (file.exists(compactUtilizationPath))
    {
        report("Reading compact Provider Pressure utilization summary...")
        utilizationSummary <- acmm_provider_pressure_read_columns(
            exportDir,
            "utilization_summary.csv",
            c(
                "lineage_id",
                "sequence",
                "created_at",
                "source_execution_id",
                "run_index",
                "subject_id",
                "scenario_id",
                "action_count",
                "utilization_sample_count",
                "first_decision_index",
                "last_decision_index",
                "mean_content_requests_per_hour_utilization",
                "max_content_requests_per_hour_utilization",
                "mean_content_requests_per_minute_utilization",
                "max_content_requests_per_minute_utilization"
            )
        )
        if (nrow(utilizationSummary) > 0)
        {
            utilizationSummary$sequence <- as.integer(utilizationSummary$sequence)
            utilizationSummary$action_count <- as.integer(utilizationSummary$action_count)
            utilizationSummary$utilization_sample_count <- as.integer(utilizationSummary$utilization_sample_count)
            utilizationSummary$first_decision_index <- as.integer(utilizationSummary$first_decision_index)
            utilizationSummary$last_decision_index <- as.integer(utilizationSummary$last_decision_index)
            numericUtilizationColumns <- c(
                "mean_content_requests_per_hour_utilization",
                "max_content_requests_per_hour_utilization",
                "mean_content_requests_per_minute_utilization",
                "max_content_requests_per_minute_utilization"
            )
            for (name in numericUtilizationColumns)
            {
                utilizationSummary[[name]] <- as.numeric(utilizationSummary[[name]])
            }
            utilizationSummary <- utilizationSummary[
                order(
                    utilizationSummary$lineage_id,
                    utilizationSummary$sequence,
                    utilizationSummary$first_decision_index,
                    utilizationSummary$subject_id,
                    utilizationSummary$run_index
                ),
                ,
                drop = FALSE
            ]
        }
    }

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
        feature_trajectory = featureTrajectory,
        admission_summary = admissionSummary,
        utilization_summary = utilizationSummary
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
            graphics::title(main = "Provider Pressure pacing burden")
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
            mar = c(5.5, 6, 3.5, 11) + 0.1,
            oma = c(0, 0, 4, 0),
            xpd = NA
        )
        for (lineageIndex in seq_along(lineages))
        {
            lineageId <- lineages[[lineageIndex]]
            panel <- rows[rows$lineage_id == lineageId, , drop = FALSE]
            sequences <- sort(unique(panel$sequence))
            matrixValues <- matrix(
                0,
                nrow = length(reasons),
                ncol = length(sequences),
                dimnames = list(reasons, as.character(sequences))
            )
            for (reasonIndex in seq_along(reasons))
            {
                matched <- panel[panel$reason == reasons[[reasonIndex]], , drop = FALSE]
                if (nrow(matched) > 0)
                {
                    aggregated <- stats::aggregate(
                        matched$pacing_seconds,
                        by = list(sequence = matched$sequence),
                        FUN = sum
                    )
                    positions <- match(aggregated$sequence, sequences)
                    matrixValues[reasonIndex, positions] <- aggregated$x
                }
            }
            ymax <- max(colSums(matrixValues), na.rm = TRUE)
            if (!is.finite(ymax) || ymax <= 0)
            {
                ymax <- 1
            }

            if (length(sequences) <= 12L)
            {
                graphics::barplot(
                    matrixValues,
                    beside = FALSE,
                    col = colours,
                    border = NA,
                    names.arg = as.character(sequences),
                    ylim = c(0, ymax * 1.08),
                    xlab = "Trajectory checkpoint sequence",
                    ylab = "Pacing seconds in checkpoint",
                    main = if (length(lineages) > 1) paste0("Lineage ", lineageId) else "",
                    las = 1
                )
            } else
            {
                graphics::plot(
                    NA,
                    xlim = range(sequences),
                    ylim = c(0, ymax * 1.08),
                    xlab = "Trajectory checkpoint sequence",
                    ylab = "Pacing seconds in checkpoint",
                    main = if (length(lineages) > 1) paste0("Lineage ", lineageId) else "",
                    las = 1
                )
                for (reasonIndex in seq_along(reasons))
                {
                    graphics::lines(
                        sequences,
                        matrixValues[reasonIndex, ],
                        type = "o",
                        pch = 16,
                        cex = 0.55,
                        lwd = 1.2,
                        col = colours[[reasonIndex]]
                    )
                }
            }
            graphics::abline(h = pretty(c(0, ymax), n = 5), col = "grey90", lty = 3)
            graphics::legend(
                "topright",
                inset = c(-0.34, 0),
                legend = reasons,
                fill = if (length(sequences) <= 12L) colours else NULL,
                col = if (length(sequences) > 12L) colours else NULL,
                lty = if (length(sequences) > 12L) 1 else NULL,
                pch = if (length(sequences) > 12L) 16 else NULL,
                bty = "n",
                cex = 0.68
            )
        }
        graphics::mtext(
            "Provider Pressure pacing burden by recorded reason",
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


acmm_plot_provider_pressure_admission_waits <- function(
    analysisRoot,
    formats = c("png", "pdf")
)
{
    rows <- utils::read.csv(
        file.path(
            analysisRoot,
            "data",
            "provider_pressure",
            "provider_pressure_admission_summary.csv"
        ),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "provider_pressure",
        "admission_waits"
    )
    rows$total_admission_wait_seconds <- as.numeric(rows$total_admission_wait_seconds)
    rows <- rows[
        nzchar(rows$subject_id) & is.finite(rows$total_admission_wait_seconds),
        ,
        drop = FALSE
    ]
    if (nrow(rows) == 0)
    {
        drawEmpty <- function()
        {
            graphics::plot.new()
            graphics::title(main = "Provider Pressure admission-wait burden")
            graphics::text(0.5, 0.5, "No run-associated admission decisions were recorded.")
        }
        acmm_render_plot(outputStem, drawEmpty, formats = formats)
        return(invisible(outputStem))
    }

    subjects <- sort(unique(rows$subject_id))
    colours <- acmm_subject_palette(subjects)
    values <- lapply(subjects, function(subject)
    {
        rows$total_admission_wait_seconds[rows$subject_id == subject]
    })
    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(mar = c(7, 6, 4, 2) + 0.1)
        graphics::boxplot(
            values,
            names = acmm_plot_subject_labels(subjects),
            col = colours,
            ylab = "Total admission wait per run (seconds)",
            main = "Provider Pressure admission-wait burden",
            las = 1,
            outline = TRUE
        )
        graphics::abline(h = pretty(c(0, max(rows$total_admission_wait_seconds, na.rm = TRUE)), n = 5), col = "grey90", lty = 3)
        graphics::mtext(
            "Each observation is one run; values sum all recorded action-admission waits for that run.",
            side = 1,
            line = 5.2,
            cex = 0.72
        )
    }
    acmm_render_plot(
        outputStem,
        draw,
        width = 1800,
        height = 1100,
        formats = formats
    )
}


acmm_plot_provider_pressure_utilization_envelope <- function(
    analysisRoot,
    formats = c("png", "pdf")
)
{
    rows <- utils::read.csv(
        file.path(
            analysisRoot,
            "data",
            "provider_pressure",
            "provider_pressure_utilization_summary.csv"
        ),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "provider_pressure",
        "utilization_envelope"
    )
    if (nrow(rows) == 0)
    {
        drawEmpty <- function()
        {
            graphics::plot.new()
            graphics::title(main = "Maximum provider utilization")
            graphics::text(0.5, 0.5, "No provider-utilization observations were recorded.")
        }
        acmm_render_plot(outputStem, drawEmpty, formats = formats)
        return(invisible(outputStem))
    }

    rows$first_decision_index <- as.numeric(rows$first_decision_index)
    rows$max_content_requests_per_hour_utilization <- as.numeric(rows$max_content_requests_per_hour_utilization)
    rows$max_content_requests_per_minute_utilization <- as.numeric(rows$max_content_requests_per_minute_utilization)
    rows <- rows[
        is.finite(rows$first_decision_index) &
            (
                is.finite(rows$max_content_requests_per_hour_utilization) |
                    is.finite(rows$max_content_requests_per_minute_utilization)
            ),
        ,
        drop = FALSE
    ]
    if (nrow(rows) == 0)
    {
        drawEmpty <- function()
        {
            graphics::plot.new()
            graphics::title(main = "Maximum provider utilization")
            graphics::text(0.5, 0.5, "No finite provider-utilization observations were recorded.")
        }
        acmm_render_plot(outputStem, drawEmpty, formats = formats)
        return(invisible(outputStem))
    }

    lineages <- sort(unique(rows$lineage_id))
    colours <- grDevices::hcl.colors(3L, palette = "Dark 3")[1:2]
    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(
            mfrow = c(length(lineages), 1),
            mar = c(6.5, 6.5, 3.5, 13.5) + 0.1,
            oma = c(2.5, 0, 4.5, 0),
            xpd = FALSE
        )
        for (lineageIndex in seq_along(lineages))
        {
            lineageId <- lineages[[lineageIndex]]
            panel <- rows[rows$lineage_id == lineageId, , drop = FALSE]
            panel <- panel[order(panel$sequence, panel$first_decision_index), , drop = FALSE]
            x <- seq_len(nrow(panel))
            hour <- 100 * panel$max_content_requests_per_hour_utilization
            minute <- 100 * panel$max_content_requests_per_minute_utilization
            observedMax <- suppressWarnings(max(c(hour, minute), na.rm = TRUE))
            if (!is.finite(observedMax))
            {
                observedMax <- 0
            }
            yMax <- max(100, ceiling(observedMax / 10) * 10)
            graphics::plot(
                NA,
                xlim = c(1, max(1, length(x))),
                ylim = c(0, yMax),
                xlab = "Run occurrence in pressure-event order",
                ylab = "Maximum utilization (% of operational threshold)",
                main = if (length(lineages) > 1) paste0("Lineage ", lineageId) else "",
                las = 1
            )
            graphics::abline(h = pretty(c(0, yMax), n = 5), col = "grey90", lty = 3)
            graphics::abline(h = 100, col = "grey45", lty = 2, lwd = 1.3)
            graphics::lines(x, hour, col = colours[[1]], lwd = 1.3, type = "o", pch = 16, cex = 0.42)
            graphics::lines(x, minute, col = colours[[2]], lwd = 1.3, type = "o", pch = 17, cex = 0.42)
            graphics::par(xpd = NA)
            graphics::legend(
                "topright",
                inset = c(-0.24, 0),
                legend = c("Requests/hour", "Requests/minute", "Operational threshold"),
                col = c(colours, "grey45"),
                lty = c(1, 1, 2),
                pch = c(16, 17, NA),
                bty = "n",
                cex = 0.72
            )
            graphics::par(xpd = FALSE)
        }
        graphics::mtext(
            "Maximum provider utilization across run occurrences",
            side = 3,
            outer = TRUE,
            line = 2,
            font = 2,
            cex = 1.05
        )
        graphics::mtext(
            "Each point is the maximum utilization observed among action admissions associated with one run occurrence; 100% is the recorded operational threshold.",
            side = 1,
            outer = TRUE,
            line = 0.8,
            cex = 0.68
        )
    }
    acmm_render_plot(
        outputStem,
        draw,
        width = 2500,
        height = max(1100, 760 * length(lineages)),
        formats = formats
    )
}


acmm_plot_provider_pressure_utilization_by_subject <- function(
    analysisRoot,
    formats = c("png", "pdf")
)
{
    rows <- utils::read.csv(
        file.path(
            analysisRoot,
            "data",
            "provider_pressure",
            "provider_pressure_utilization_summary.csv"
        ),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "provider_pressure",
        "utilization_by_subject"
    )
    if (nrow(rows) == 0)
    {
        drawEmpty <- function()
        {
            graphics::plot.new()
            graphics::title(main = "Provider utilization by subject")
            graphics::text(0.5, 0.5, "No provider-utilization observations were recorded.")
        }
        acmm_render_plot(outputStem, drawEmpty, formats = formats)
        return(invisible(outputStem))
    }

    rows$max_content_requests_per_hour_utilization <- 100 * as.numeric(rows$max_content_requests_per_hour_utilization)
    rows$max_content_requests_per_minute_utilization <- 100 * as.numeric(rows$max_content_requests_per_minute_utilization)
    rows <- rows[nzchar(rows$subject_id), , drop = FALSE]
    subjects <- sort(unique(rows$subject_id))
    colours <- acmm_subject_palette(subjects)
    labels <- acmm_plot_subject_labels(subjects)
    metrics <- c(
        max_content_requests_per_hour_utilization = "Requests/hour",
        max_content_requests_per_minute_utilization = "Requests/minute"
    )
    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(mfrow = c(1, 2), mar = c(7, 6, 4, 2) + 0.1, oma = c(0, 0, 4, 0))
        for (metric in names(metrics))
        {
            values <- lapply(subjects, function(subject)
            {
                value <- rows[[metric]][rows$subject_id == subject]
                value[is.finite(value)]
            })
            observedMax <- suppressWarnings(max(unlist(values), na.rm = TRUE))
            if (!is.finite(observedMax))
            {
                observedMax <- 0
            }
            yMax <- max(100, ceiling(observedMax / 10) * 10)
            graphics::boxplot(
                values,
                names = labels,
                col = colours,
                ylim = c(0, yMax),
                ylab = "Maximum utilization per run (% of threshold)",
                main = metrics[[metric]],
                las = 1,
                outline = TRUE
            )
            graphics::abline(h = pretty(c(0, yMax), n = 5), col = "grey90", lty = 3)
            graphics::abline(h = 100, col = "grey45", lty = 2, lwd = 1.2)
        }
        graphics::mtext(
            "Provider utilization distribution by subject",
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
        height = 1100,
        formats = formats
    )
}


acmm_plot_provider_pressure_wait_vs_actions <- function(
    analysisRoot,
    formats = c("png", "pdf")
)
{
    rows <- utils::read.csv(
        file.path(
            analysisRoot,
            "data",
            "provider_pressure",
            "provider_pressure_admission_summary.csv"
        ),
        stringsAsFactors = FALSE,
        check.names = FALSE
    )
    outputStem <- file.path(
        analysisRoot,
        "figures",
        "provider_pressure",
        "admission_wait_vs_actions"
    )
    rows$action_count <- as.numeric(rows$action_count)
    rows$total_admission_wait_seconds <- as.numeric(rows$total_admission_wait_seconds)
    rows <- rows[
        nzchar(rows$subject_id) &
            is.finite(rows$action_count) &
            is.finite(rows$total_admission_wait_seconds),
        ,
        drop = FALSE
    ]
    if (nrow(rows) == 0)
    {
        drawEmpty <- function()
        {
            graphics::plot.new()
            graphics::title(main = "Admission wait versus action volume")
            graphics::text(0.5, 0.5, "No run-associated admission decisions were recorded.")
        }
        acmm_render_plot(outputStem, drawEmpty, formats = formats)
        return(invisible(outputStem))
    }

    subjects <- sort(unique(rows$subject_id))
    colours <- acmm_subject_palette(subjects)
    draw <- function()
    {
        oldPar <- graphics::par(no.readonly = TRUE)
        on.exit(graphics::par(oldPar), add = TRUE)
        graphics::par(mar = c(6.5, 6.5, 4, 12.5) + 0.1, xpd = FALSE)
        graphics::plot(
            NA,
            xlim = range(rows$action_count),
            ylim = range(c(0, rows$total_admission_wait_seconds)),
            xlab = "Provider-managed actions in run occurrence",
            ylab = "Total admission wait (seconds)",
            main = "Admission wait versus action volume",
            las = 1
        )
        graphics::abline(h = pretty(range(c(0, rows$total_admission_wait_seconds)), n = 5), col = "grey90", lty = 3)
        for (index in seq_along(subjects))
        {
            subject <- subjects[[index]]
            panel <- rows[rows$subject_id == subject, , drop = FALSE]
            graphics::points(
                panel$action_count,
                panel$total_admission_wait_seconds,
                pch = 16,
                cex = 0.75,
                col = grDevices::adjustcolor(colours[[index]], alpha.f = 0.7)
            )
        }
        graphics::par(xpd = NA)
        graphics::legend(
            "topright",
            inset = c(-0.22, 0),
            legend = acmm_plot_subject_labels(subjects),
            col = colours,
            pch = 16,
            bty = "n",
            cex = 0.75
        )
        graphics::mtext(
            "Each point is one run occurrence; no fitted model is shown.",
            side = 1,
            line = 5.1,
            cex = 0.72
        )
    }
    acmm_render_plot(
        outputStem,
        draw,
        width = 2300,
        height = 1200,
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
