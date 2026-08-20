import pytest
from pydantic import ValidationError

from app.answering.evidence_dto import (
    EvidenceItem,
    EvidenceReason,
    EvidenceState,
    EvidenceVerificationInput,
    EvidenceVerificationResult,
)


def make_evidence() -> EvidenceItem:
    return EvidenceItem(
        source_id="SOURCE_1",
        source_type="document_chunk",
        source_ref="chunk-1",
        title="techpilot.md",
        locator="section=Retrieval",
        text="TechPilot uses multilingual-e5-base for dense retrieval.",
    )


def test_evidence_state_has_stable_string_values() -> None:
    assert EvidenceState.SUFFICIENT.value == "sufficient"
    assert EvidenceState.INSUFFICIENT.value == "insufficient"
    assert EvidenceState.CONFLICTING.value == "conflicting"


def test_evidence_reason_has_stable_string_values() -> None:
    assert EvidenceReason.NO_EVIDENCE.value == "no_evidence"
    assert EvidenceReason.SUBJECT_MISMATCH.value == "subject_mismatch"
    assert EvidenceReason.ATTRIBUTE_MISSING.value == "attribute_missing"
    assert EvidenceReason.RELATION_MISSING.value == "relation_missing"
    assert (
        EvidenceReason.CONFLICTING_EVIDENCE.value
        == "conflicting_evidence"
    )


def test_verification_input_preserves_target_and_evidence() -> None:
    evidence = make_evidence()

    request = EvidenceVerificationInput(
        target="Which embedding model does TechPilot use?",
        evidence=(evidence,),
    )

    assert request.target == "Which embedding model does TechPilot use?"
    assert request.evidence == (evidence,)
    assert request.evidence[0].source_type == "document_chunk"
    assert request.evidence[0].source_ref == "chunk-1"


def test_verification_result_preserves_structured_state() -> None:
    result = EvidenceVerificationResult(
        state=EvidenceState.INSUFFICIENT,
        reasons=(EvidenceReason.RELATION_MISSING,),
        supporting_source_ids=(),
        conflicting_source_ids=(),
        explanation=(
            "The evidence mentions the model but does not establish "
            "that TechPilot uses it."
        ),
    )

    assert result.state is EvidenceState.INSUFFICIENT
    assert result.reasons == (EvidenceReason.RELATION_MISSING,)
    assert result.supporting_source_ids == ()
    assert result.conflicting_source_ids == ()


def test_evidence_contract_is_immutable() -> None:
    evidence = make_evidence()

    with pytest.raises(ValidationError, match="frozen_instance"):
        evidence.text = "changed"  # type: ignore[misc]


def test_evidence_contract_exports_json_schema_for_future_tool_mapping() -> None:
    input_schema = EvidenceVerificationInput.model_json_schema()
    output_schema = EvidenceVerificationResult.model_json_schema()

    assert input_schema["type"] == "object"
    assert output_schema["type"] == "object"
    assert "target" in input_schema["properties"]
    assert "state" in output_schema["properties"]
