import pytest

from app.answering.evidence_dto import (
    EvidenceReason,
    EvidenceState,
    EvidenceVerificationResult,
)
from scripts.evidence_verifier_smoke import assert_case, build_cases


def test_smoke_cases_cover_core_evidence_states() -> None:
    cases = build_cases()

    assert [case.name for case in cases] == [
        "sufficient",
        "subject_mismatch",
        "conflicting",
    ]
    assert [case.expected_state for case in cases] == [
        EvidenceState.SUFFICIENT,
        EvidenceState.INSUFFICIENT,
        EvidenceState.CONFLICTING,
    ]


def test_smoke_assertion_rejects_missing_required_reason() -> None:
    case = build_cases()[1]
    result = EvidenceVerificationResult(
        state=EvidenceState.INSUFFICIENT,
        reasons=(EvidenceReason.RELATION_MISSING,),
        supporting_source_ids=(),
        conflicting_source_ids=(),
        explanation="Wrong failure type.",
    )

    with pytest.raises(AssertionError, match="missing required reasons"):
        assert_case(case, result=result)
