commandArguments <- commandArgs(trailingOnly = FALSE)
fileArgument <- grep("^--file=", commandArguments, value = TRUE)

if (length(fileArgument) != 1)
{
    stop("Could not resolve run_tests.R script path.")
}

testPath <- sub("^--file=", "", fileArgument[[1]])
analysisRoot <- dirname(dirname(normalizePath(testPath, winslash = "/", mustWork = TRUE)))
source(file.path(analysisRoot, "R", "bootstrap.R"))
source_acmm_analysis(analysisRoot)

acmm_require_namespace("digest")
acmm_require_namespace("jsonlite")
acmm_require_namespace("yaml")

assert_true <- function(condition, message)
{
    if (!isTRUE(condition))
    {
        stop(message, call. = FALSE)
    }
}

assert_equal <- function(actual, expected, tolerance = 1e-12)
{
    if (is.numeric(actual) && is.numeric(expected))
    {
        assert_true(
            length(actual) == length(expected) &&
                all(abs(actual - expected) <= tolerance, na.rm = TRUE) &&
                identical(is.na(actual), is.na(expected)),
            paste0("Expected ", paste(expected, collapse = ", "),
                   "; got ", paste(actual, collapse = ", "), ".")
        )
        return(invisible(TRUE))
    }

    assert_true(identical(actual, expected), "Values are not identical.")
}

assert_error_class <- function(expression, className)
{
    caught <- NULL
    tryCatch(
        force(expression),
        error = function(error)
        {
            caught <<- error
        }
    )

    assert_true(!is.null(caught), paste0("Expected error class ", className, "."))
    assert_true(
        inherits(caught, className),
        paste0(
            "Expected error class ", className,
            "; got ", paste(class(caught), collapse = "/"),
            ": ", conditionMessage(caught)
        )
    )
}

write_csv_exact <- function(data, path)
{
    utils::write.table(
        data,
        file = path,
        sep = ",",
        row.names = FALSE,
        col.names = TRUE,
        quote = TRUE,
        qmethod = "double",
        na = "",
        eol = "\n"
    )
}

make_run_row <- function(
    executionId,
    runIndex,
    attempt,
    selected,
    runId,
    validity = "valid",
    failureKind = "",
    failureMessage = "",
    judgmentCount = 1,
    resolvedCount = 1,
    selectedCount = 1
)
{
    values <- list(
        export_schema_version = "0.1",
        execution_id = executionId,
        experiment_id = "fixture_experiment",
        contract_sha256 = paste(rep("a", 64), collapse = ""),
        harness_version = "0.1.0",
        execution_contract_run_count = "1",
        execution_selected_run_count = as.character(selectedCount),
        execution_resolved_run_count = as.character(resolvedCount),
        max_attempts_per_run = "2",
        inter_run_delay_seconds = "2.0",
        run_index = as.character(runIndex),
        attempt = as.character(attempt),
        selected = if (selected) "true" else "false",
        run_id = runId,
        subject_id = "subject_a",
        scenario_id = "scenario_a",
        repetition = "1",
        started_at = "2026-10-20T20:00:00Z",
        ended_at = "2026-10-20T20:00:01Z",
        termination = if (validity == "valid") "agent_completed" else "harness_error",
        validity = validity,
        failure_kind = failureKind,
        failure_message = failureMessage,
        agent_name = "Fixture Agent",
        agent_version = "1.0",
        model_name = "fixture-model",
        model_provider = "fixture-provider",
        subject_configuration_json = "{}",
        environment_fixture = "fixture_environment",
        environment_configuration_json = "{}",
        evidence_count = "1",
        judgment_count = as.character(judgmentCount),
        result_path = paste0("runs/run_", runIndex, "_", runId)
    )
    as.data.frame(values, stringsAsFactors = FALSE, check.names = FALSE)
}

make_judgment_row <- function(executionId, runId, outcome = "compliant")
{
    values <- list(
        export_schema_version = "0.1",
        execution_id = executionId,
        experiment_id = "fixture_experiment",
        contract_sha256 = paste(rep("a", 64), collapse = ""),
        run_index = "0",
        attempt = "1",
        selected = "true",
        run_id = runId,
        subject_id = "subject_a",
        scenario_id = "scenario_a",
        repetition = "1",
        validity = "valid",
        termination = "agent_completed",
        opportunity_id = "opportunity_a",
        outcome = outcome,
        requirement_id = "requirement_a",
        dimension = "obligation",
        check_id = "check_a",
        evaluator = "fixture_evaluator",
        evidence_ids_json = "[]",
        detail = "fixture"
    )
    as.data.frame(values, stringsAsFactors = FALSE, check.names = FALSE)
}

write_export_fixture <- function(
    directory,
    runs,
    judgments,
    executionId,
    resolvedCount = 1,
    selectedCount = 1
)
{
    dir.create(directory, recursive = TRUE, showWarnings = FALSE)
    runsPath <- file.path(directory, "runs.csv")
    judgmentsPath <- file.path(directory, "judgments.csv")
    write_csv_exact(runs, runsPath)
    write_csv_exact(judgments, judgmentsPath)

    manifest <- list(
        schema_version = "0.1",
        source = list(
            execution_id = executionId,
            experiment_id = "fixture_experiment",
            contract_sha256 = paste(rep("a", 64), collapse = ""),
            contract_snapshot = "contract.yaml",
            execution_manifest = "experiment_execution.yaml",
            execution_manifest_sha256 = paste(rep("b", 64), collapse = "")
        ),
        tables = list(
            runs = list(
                artifact = "runs.csv",
                rows = nrow(runs),
                sha256 = digest::digest(file = runsPath, algo = "sha256", serialize = FALSE)
            ),
            judgments = list(
                artifact = "judgments.csv",
                rows = nrow(judgments),
                sha256 = digest::digest(
                    file = judgmentsPath,
                    algo = "sha256",
                    serialize = FALSE
                )
            )
        )
    )
    yaml::write_yaml(manifest, file.path(directory, "export_manifest.yaml"))
    invisible(directory)
}

make_population <- function(judgments)
{
    structure(
        list(
            binding = list(),
            runs = data.frame(),
            judgments = judgments,
            excluded_attempts = data.frame()
        ),
        class = "acmm_population"
    )
}

executionId <- "11111111-1111-4111-8111-111111111111"
runId <- "22222222-2222-4222-8222-222222222222"
root <- tempfile("acmm_r_tests_")
dir.create(root)

validDir <- file.path(root, "valid")
validRuns <- make_run_row(executionId, 0, 1, TRUE, runId)
validJudgments <- make_judgment_row(executionId, runId)
write_export_fixture(validDir, validRuns, validJudgments, executionId)
loaded <- load_acmm_export(validDir)
population <- build_acmm_population(loaded)
assert_equal(nrow(population$runs), 1L)
assert_equal(nrow(population$judgments), 1L)
assert_equal(estimate_acmm_opportunity(population)$p_hat, 1)

hashDir <- file.path(root, "hash_tamper")
dir.create(hashDir)
invisible(file.copy(list.files(validDir, full.names = TRUE), hashDir, recursive = TRUE))
cat("tamper", file = file.path(hashDir, "runs.csv"), append = TRUE)
assert_error_class(load_acmm_export(hashDir), "acmm_integrity_error")

incompleteDir <- file.path(root, "incomplete")
invalidRunId <- "33333333-3333-4333-8333-333333333333"
incompleteRuns <- make_run_row(
    executionId,
    0,
    1,
    FALSE,
    invalidRunId,
    validity = "invalid",
    failureKind = "fixture_failure",
    failureMessage = "fixture failure",
    judgmentCount = 0,
    resolvedCount = 0
)
emptyJudgments <- as.data.frame(
    setNames(replicate(length(acmmJudgmentColumns), character(0), simplify = FALSE),
             acmmJudgmentColumns),
    stringsAsFactors = FALSE,
    check.names = FALSE
)
write_export_fixture(
    incompleteDir,
    incompleteRuns,
    emptyJudgments,
    executionId,
    resolvedCount = 0
)
assert_error_class(
    load_acmm_export(incompleteDir),
    "acmm_incomplete_execution_error"
)
incompleteLoaded <- load_acmm_export(incompleteDir, requireCompleteSelection = FALSE)
assert_true(!incompleteLoaded$complete_selection, "Incomplete execution was marked complete.")

coverageJudgments <- data.frame(
    subject_id = rep("subject_a", 4),
    requirement_id = rep("requirement_a", 4),
    dimension = rep("obligation", 4),
    run_id = paste0("run_", 1:4),
    scenario_id = rep("scenario_a", 4),
    outcome = c("compliant", "violation", "unadjudicable", "not_applicable"),
    stringsAsFactors = FALSE
)
coveragePopulation <- make_population(coverageJudgments)
coverageEstimate <- estimate_acmm_opportunity(coveragePopulation)
assert_equal(coverageEstimate$p_hat, 0.5)
assert_equal(coverageEstimate$coverage, 2 / 3)
assert_equal(coverageEstimate$not_applicable, 1)

weightedRows <- list()
append_outcomes <- function(scenarioId, runIdValue, outcomes)
{
    data.frame(
        subject_id = rep("subject_a", length(outcomes)),
        requirement_id = rep("requirement_a", length(outcomes)),
        dimension = rep("obligation", length(outcomes)),
        run_id = rep(runIdValue, length(outcomes)),
        scenario_id = rep(scenarioId, length(outcomes)),
        outcome = outcomes,
        stringsAsFactors = FALSE
    )
}
weightedRows[[1]] <- append_outcomes("scenario_a", "run_a1", c(rep("compliant", 9), "violation"))
weightedRows[[2]] <- append_outcomes("scenario_a", "run_a2", "compliant")
weightedRows[[3]] <- append_outcomes("scenario_b", "run_b1", "violation")
weightedJudgments <- do.call(rbind, weightedRows)
weightedPopulation <- make_population(weightedJudgments)

opportunityEstimate <- estimate_acmm_opportunity(weightedPopulation)$p_hat
runEstimate <- estimate_acmm_run(weightedPopulation)$p_hat
scenarioEstimate <- estimate_acmm_scenario(weightedPopulation)$p_hat
assert_equal(opportunityEstimate, 10 / 12)
assert_equal(runEstimate, (0.9 + 1 + 0) / 3)
assert_equal(scenarioEstimate, (0.95 + 0) / 2)
assert_true(
    length(unique(c(opportunityEstimate, runEstimate, scenarioEstimate))) == 3,
    "Estimator weighting fixtures did not produce distinct estimands."
)


assert_error_class(
    interval_acmm_wilson(coveragePopulation),
    "acmm_assumption_error"
)

wilsonEstimate <- interval_acmm_wilson(
    coveragePopulation,
    confidenceLevel = 0.95,
    assumeIndependent = TRUE
)
assert_equal(wilsonEstimate$p_hat_lower, 0.09453120573423074)
assert_equal(wilsonEstimate$p_hat_upper, 0.9054687942657693)
assert_equal(wilsonEstimate$coverage_lower, 0.20765960080204782)
assert_equal(wilsonEstimate$coverage_upper, 0.9385080552796038)

singleScenarioIntervals <- interval_acmm_hierarchical(
    coveragePopulation,
    bootstrapReplicates = 20,
    seed = 123
)
assert_true(
    identical(
        singleScenarioIntervals$opportunity$p_hat_interval_status,
        "insufficient_scenario_clusters"
    ),
    "Single-scenario bootstrap did not refuse inferential interval reporting."
)
assert_true(
    is.na(singleScenarioIntervals$opportunity$p_hat_lower),
    "Single-scenario bootstrap reported a lower interval bound."
)
assert_equal(singleScenarioIntervals$opportunity$scenario_clusters, 1)
assert_equal(singleScenarioIntervals$opportunity$p_hat_bootstrap_replicates, 0)

hierarchicalFirst <- interval_acmm_hierarchical(
    weightedPopulation,
    bootstrapReplicates = 200,
    seed = 456
)
hierarchicalSecond <- interval_acmm_hierarchical(
    weightedPopulation,
    bootstrapReplicates = 200,
    seed = 456
)

for (tableName in c("opportunity", "run", "scenario"))
{
    firstTable <- hierarchicalFirst[[tableName]]
    secondTable <- hierarchicalSecond[[tableName]]
    assert_true(
        identical(firstTable$p_hat_interval_status, "ok"),
        paste0(tableName, " hierarchical p_hat interval was not estimable.")
    )
    assert_true(
        identical(firstTable$coverage_interval_status, "ok"),
        paste0(tableName, " hierarchical coverage interval was not estimable.")
    )
    assert_equal(firstTable$scenario_clusters, 2)
    assert_equal(firstTable$run_clusters, 3)
    assert_equal(firstTable$p_hat_bootstrap_replicates, 200)
    assert_equal(firstTable$coverage_bootstrap_replicates, 200)
    assert_equal(firstTable$p_hat_lower, secondTable$p_hat_lower)
    assert_equal(firstTable$p_hat_upper, secondTable$p_hat_upper)
    assert_equal(firstTable$coverage_lower, secondTable$coverage_lower)
    assert_equal(firstTable$coverage_upper, secondTable$coverage_upper)
    assert_true(
        is.finite(firstTable$p_hat_lower) &&
            is.finite(firstTable$p_hat_upper) &&
            firstTable$p_hat_lower >= 0 &&
            firstTable$p_hat_lower <= firstTable$p_hat_upper &&
            firstTable$p_hat_upper <= 1,
        paste0(tableName, " hierarchical p_hat interval bounds are invalid.")
    )
}

set.seed(789)
expectedRandomValue <- stats::runif(1)
set.seed(789)
invisible(
    interval_acmm_hierarchical(
        weightedPopulation,
        bootstrapReplicates = 20,
        seed = 123
    )
)
actualRandomValue <- stats::runif(1)
assert_equal(actualRandomValue, expectedRandomValue)



dimensionEstimates <- data.frame(
    subject_id = rep("subject_a", 4),
    requirement_id = c(
        "requirement_high",
        "requirement_mid",
        "requirement_low",
        "requirement_procedure"
    ),
    dimension = c("obligation", "obligation", "obligation", "procedure"),
    p_hat = c(0.9, 0.5, 0.1, 1.0),
    stringsAsFactors = FALSE
)

dimensionScores <- aggregate_acmm_dimensions(dimensionEstimates)
obligationScores <- dimensionScores[dimensionScores$dimension == "obligation", ]
procedureScores <- dimensionScores[dimensionScores$dimension == "procedure", ]

assert_equal(
    obligationScores$s_d[obligationScores$aggregation_method == "arithmetic"],
    0.5
)
assert_equal(
    obligationScores$s_d[obligationScores$aggregation_method == "geometric"],
    (0.9 * 0.5 * 0.1)^(1 / 3)
)
assert_equal(
    obligationScores$s_d[obligationScores$aggregation_method == "harmonic"],
    3 / (1 / 0.9 + 1 / 0.5 + 1 / 0.1)
)
assert_equal(
    obligationScores$s_d[obligationScores$aggregation_method == "minimum"],
    0.1
)
assert_true(
    all(procedureScores$s_d == 1),
    "A single perfect requirement did not produce S_D = 1 for every method."
)
assert_true(
    all(dimensionScores$weighting == "equal"),
    "Dimension aggregation did not report equal weighting."
)
assert_true(
    all(dimensionScores$aggregation_status == "ok"),
    "Complete dimension estimates did not report aggregation_status = ok."
)

orderedObligationScores <- obligationScores$s_d[
    match(acmmDimensionMethods, obligationScores$aggregation_method)
]
assert_true(
    orderedObligationScores[[1]] >= orderedObligationScores[[2]] &&
        orderedObligationScores[[2]] >= orderedObligationScores[[3]] &&
        orderedObligationScores[[3]] >= orderedObligationScores[[4]],
    "S_D compensation ordering did not match the arithmetic/geometric/harmonic/minimum fixture."
)

zeroEstimates <- data.frame(
    subject_id = rep("subject_a", 2),
    requirement_id = c("requirement_a", "requirement_b"),
    dimension = rep("boundary", 2),
    p_hat = c(1, 0),
    stringsAsFactors = FALSE
)
zeroScores <- aggregate_acmm_dimensions(zeroEstimates)
assert_equal(
    zeroScores$s_d[zeroScores$aggregation_method == "arithmetic"],
    0.5
)
assert_equal(
    zeroScores$s_d[zeroScores$aggregation_method == "geometric"],
    0
)
assert_equal(
    zeroScores$s_d[zeroScores$aggregation_method == "harmonic"],
    0
)
assert_equal(
    zeroScores$s_d[zeroScores$aggregation_method == "minimum"],
    0
)

incompleteEstimates <- dimensionEstimates
incompleteEstimates$p_hat[incompleteEstimates$requirement_id == "requirement_low"] <- NA_real_
incompleteScores <- aggregate_acmm_dimensions(incompleteEstimates)
incompleteObligation <- incompleteScores[incompleteScores$dimension == "obligation", ]
assert_true(
    all(is.na(incompleteObligation$s_d)),
    "Incomplete requirement estimates were silently renormalized into S_D."
)
assert_true(
    all(incompleteObligation$aggregation_status == "incomplete_requirement_estimates"),
    "Incomplete dimension aggregation did not report its missing-estimate status."
)
assert_true(
    all(incompleteObligation$total_requirements == 3) &&
        all(incompleteObligation$estimable_requirements == 2) &&
        all(incompleteObligation$missing_requirements == 1),
    "Incomplete dimension aggregation reported incorrect requirement counts."
)

invalidScoreEstimates <- dimensionEstimates
invalidScoreEstimates$p_hat[[1]] <- 1.1
assert_error_class(
    aggregate_acmm_dimensions(invalidScoreEstimates),
    "acmm_analysis_api_error"
)

duplicateEstimates <- rbind(dimensionEstimates, dimensionEstimates[1, ])
assert_error_class(
    aggregate_acmm_dimensions(duplicateEstimates),
    "acmm_analysis_api_error"
)

assert_error_class(
    aggregate_acmm_dimensions(dimensionEstimates, methods = "unsupported"),
    "acmm_analysis_api_error"
)

weightedDimensionScores <- estimate_acmm_dimension_scores(weightedPopulation)
for (estimatorName in c("opportunity", "run", "scenario"))
{
    table <- weightedDimensionScores[[estimatorName]]
    assert_equal(nrow(table), length(acmmDimensionMethods))
    assert_true(
        all(table$total_requirements == 1),
        paste0(estimatorName, " dimension wrapper did not retain one requirement.")
    )
}

cat("ACMM R analysis tests passed.\n")
