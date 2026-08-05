from pathlib import Path

import pytest

from app.answering.evidence_dto import EvidenceReason, EvidenceState
from scripts.evidence_verifier_eval import (
    EvidenceEvaluationCase,
    EvidenceEvaluationResult,
    load_cases,
    summarize_results,
    write_results,
)


def make_case(
    *,
    case_id: str,
    expected_state: EvidenceState,
    expected_reasons: tuple[EvidenceReason, ...],
) -> EvidenceEvaluationCase:
    return EvidenceEvaluationCase(
        id=case_id,
        target="Target",
        evidence=(),
        expected_state=expected_state,
        expected_reasons=expected_reasons,
        notes="",
    )


def make_result(
    *,
    case: EvidenceEvaluationCase,
    actual_state: EvidenceState | None,
    actual_reasons: tuple[EvidenceReason, ...],
    error: str | None = None,
) -> EvidenceEvaluationResult:
    return EvidenceEvaluationResult(
        case=case,
        actual_state=actual_state,
        actual_reasons=actual_reasons,
        supporting_source_ids=(),
        conflicting_source_ids=(),
        explanation="test" if error is None else None,
        error=error,
    )


def test_summarize_results_keeps_runtime_errors_out_of_accuracy_denominator() -> None:
    sufficient = make_case(
        case_id="s",
        expected_state=EvidenceState.SUFFICIENT,
        expected_reasons=(),
    )
    insufficient = make_case(
        case_id="i",
        expected_state=EvidenceState.INSUFFICIENT,
        expected_reasons=(EvidenceReason.RELATION_MISSING,),
    )
    conflicting = make_case(
        case_id="c",
        expected_state=EvidenceState.CONFLICTING,
        expected_reasons=(EvidenceReason.CONFLICTING_EVIDENCE,),
    )

    summary = summarize_results(
        [
            make_result(
                case=sufficient,
                actual_state=EvidenceState.SUFFICIENT,
                actual_reasons=(),
            ),
            make_result(
                case=insufficient,
                actual_state=EvidenceState.INSUFFICIENT,
                actual_reasons=(EvidenceReason.ATTRIBUTE_MISSING,),
            ),
            make_result(
                case=conflicting,
                actual_state=None,
                actual_reasons=(),
                error="RuntimeError: failed",
            ),
        ]
    )

    assert summary.total_cases == 3
    assert summary.runtime_errors == 1
    assert summary.evaluated_cases == 2
    assert summary.state_correct == 2
    assert summary.state_accuracy == pytest.approx(1.0)
    assert summary.reason_exact_matches == 1
    assert summary.reason_exact_match_rate == pytest.approx(0.5)


def test_load_cases_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    line = (
        '{"id":"case-1","target":"Target","evidence":[], '
        '"expected_state":"insufficient",'
        '"expected_reasons":["no_evidence"]}'
    )
    path.write_text(line + "\n" + line + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate case id"):
        load_cases(path)


def test_write_results_serializes_pydantic_evidence_items(tmp_path: Path) -> None:
    from app.answering.evidence_dto import EvidenceItem

    case = EvidenceEvaluationCase(
        id="case-1",
        target="What is configured?",
        evidence=(
            EvidenceItem(
                source_id="SOURCE_1",
                text="The configured value is A.",
                source_type="document",
                source_ref="chunk-1",
                title="doc.md",
                locator="section=Config",
            ),
        ),
        expected_state=EvidenceState.SUFFICIENT,
        expected_reasons=(),
        notes="",
    )
    result = EvidenceEvaluationResult(
        case=case,
        actual_state=EvidenceState.SUFFICIENT,
        actual_reasons=(),
        supporting_source_ids=("SOURCE_1",),
        conflicting_source_ids=(),
        explanation="Supported.",
        error=None,
    )
    path = tmp_path / "results.jsonl"

    write_results(path=path, results=[result])

    payload = path.read_text(encoding="utf-8")
    assert '"source_id": "SOURCE_1"' in payload
    assert '"source_ref": "chunk-1"' in payload
