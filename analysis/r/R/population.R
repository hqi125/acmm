build_acmm_population <- function(exportData)
{
    acmm_assert(
        inherits(exportData, "acmm_export"),
        "build_acmm_population() requires a validated acmm_export object.",
        "acmm_analysis_api_error"
    )

    selectedMask <- exportData$runs$selected & exportData$runs$validity == "valid"
    selectedRuns <- exportData$runs[selectedMask, , drop = FALSE]
    excludedAttempts <- exportData$runs[!selectedMask, , drop = FALSE]

    selectedRunIds <- selectedRuns$run_id
    judgmentMask <- exportData$judgments$run_id %in% selectedRunIds
    selectedJudgments <- exportData$judgments[judgmentMask, , drop = FALSE]

    if (nrow(selectedJudgments) > 0)
    {
        acmm_assert(
            all(selectedJudgments$selected & selectedJudgments$validity == "valid"),
            "Statistical population contains a non-selected or invalid judgment.",
            "acmm_integrity_error"
        )
    }

    expectedJudgments <- sum(selectedRuns$judgment_count)
    acmm_assert(
        nrow(selectedJudgments) == expectedJudgments,
        "Selected judgment population does not match selected Run Record counts.",
        "acmm_integrity_error"
    )

    structure(
        list(
            binding = exportData$binding,
            runs = selectedRuns,
            judgments = selectedJudgments,
            excluded_attempts = excludedAttempts
        ),
        class = "acmm_population"
    )
}
