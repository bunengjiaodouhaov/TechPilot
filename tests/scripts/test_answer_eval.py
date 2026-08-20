from scripts.answer_eval import (
    AnswerEvaluationCase,
    AnswerEvaluationResult,
    format_rate,
    summarize_results,
)


def make_case(
    *,
    case_id: str,
    answerable: bool,
) -> AnswerEvaluationCase:
    return AnswerEvaluationCase(
        id=case_id,
        question=f"Question for {case_id}",
        workspace_id=1,
        answerable=answerable,
        reference_answer=("Reference" if answerable else None),
        expected_document_names=(),
        acceptance_criteria=(),
        category="test",
        notes="",
    )


def make_result(
    *,
    case_id: str,
    answerable: bool,
    refused: bool | None,
    error: str | None = None,
) -> AnswerEvaluationResult:
    return AnswerEvaluationResult(
        case=make_case(
            case_id=case_id,
            answerable=answerable,
        ),
        answer_text=(None if error is not None else "Result"),
        refused=refused,
        citations=(),
        error=error,
    )


def test_summarize_results_separates_refusal_failure_types() -> None:
    summary = summarize_results(
        [
            make_result(
                case_id="answerable-answered",
                answerable=True,
                refused=False,
            ),
            make_result(
                case_id="answerable-refused",
                answerable=True,
                refused=True,
            ),
            make_result(
                case_id="unanswerable-refused-1",
                answerable=False,
                refused=True,
            ),
            make_result(
                case_id="unanswerable-refused-2",
                answerable=False,
                refused=True,
            ),
            make_result(
                case_id="unanswerable-answered",
                answerable=False,
                refused=False,
            ),
            make_result(
                case_id="unanswerable-error",
                answerable=False,
                refused=None,
                error="RuntimeError: unavailable",
            ),
        ]
    )

    assert summary.total_cases == 6
    assert summary.runtime_errors == 1

    assert summary.answerable_cases == 2
    assert summary.evaluated_answerable_cases == 2
    assert summary.over_refusals == 1
    assert summary.over_refusal_rate == 0.5

    assert summary.unanswerable_cases == 4
    assert summary.evaluated_unanswerable_cases == 3
    assert summary.correct_refusals == 2
    assert summary.incorrect_answers == 1
    assert summary.incorrect_answer_rate == 1 / 3


def test_summarize_results_does_not_treat_runtime_error_as_answer() -> None:
    summary = summarize_results(
        [
            make_result(
                case_id="unanswerable-error",
                answerable=False,
                refused=None,
                error="TimeoutError: timed out",
            )
        ]
    )

    assert summary.runtime_errors == 1
    assert summary.unanswerable_cases == 1
    assert summary.evaluated_unanswerable_cases == 0
    assert summary.correct_refusals == 0
    assert summary.incorrect_answers == 0
    assert summary.incorrect_answer_rate is None


def test_format_rate_preserves_missing_evidence() -> None:
    assert format_rate(None) == "n/a"
    assert format_rate(0.2) == "0.200000"
