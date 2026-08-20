import pytest
from pydantic import ValidationError

from app.answering.evidence_dto import (
    EvidenceItem,
    EvidenceState,
    EvidenceVerificationInput,
    EvidenceVerificationResult,
)
from app.answering.evidence_verifier import (
    build_evidence_verification_prompt,
    validate_evidence_verification_result,
)


def make_item(*, source_id: str = "SOURCE_1", text: str = "Evidence text") -> EvidenceItem:
    return EvidenceItem(
        source_id=source_id,
        text=text,
        source_type="document",
        source_ref="chunk-1",
        title="doc.md",
        locator="section=Section",
    )


def test_build_prompt_uses_only_supplied_target_and_evidence() -> None:
    prompt = build_evidence_verification_prompt(
        request=EvidenceVerificationInput(
            target=" Target ",
            evidence=(make_item(text=" Authoritative evidence "),),
        )
    )

    assert "Target" in prompt
    assert "[SOURCE_1]" in prompt
    assert "Authoritative evidence" in prompt
    assert "Return only the required JSON" in prompt


def test_build_prompt_allows_empty_evidence_for_deterministic_no_evidence_state() -> None:
    prompt = build_evidence_verification_prompt(
        request=EvidenceVerificationInput(target="Target", evidence=())
    )
    assert "Evidence:\n\n(none)" in prompt


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"target": " ", "evidence": ()}, "target must not be empty"),
        (
            {
                "target": "Target",
                "evidence": (
                    {
                        "source_id": " ",
                        "text": "Evidence",
                        "source_type": "document",
                        "source_ref": "chunk-1",
                    },
                ),
            },
            "evidence source_id must not be empty",
        ),
        (
            {
                "target": "Target",
                "evidence": (
                    {
                        "source_id": "SOURCE_1",
                        "text": " ",
                        "source_type": "document",
                        "source_ref": "chunk-1",
                    },
                ),
            },
            "evidence text must not be empty",
        ),
        (
            {
                "target": "Target",
                "evidence": (make_item(), make_item()),
            },
            "duplicate evidence source_id",
        ),
    ],
)
def test_verification_input_rejects_invalid_contract(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        EvidenceVerificationInput(**kwargs)  # type: ignore[arg-type]


def test_validate_result_rejects_unknown_supporting_source() -> None:
    request = EvidenceVerificationInput(target="Target", evidence=(make_item(),))
    result = EvidenceVerificationResult(
        state=EvidenceState.SUFFICIENT,
        reasons=(),
        supporting_source_ids=("SOURCE_99",),
        conflicting_source_ids=(),
        explanation="Invalid source.",
    )

    with pytest.raises(ValueError, match="unknown sources: SOURCE_99"):
        validate_evidence_verification_result(request=request, result=result)


def test_evidence_contract_exports_json_schema_for_future_tool_contract() -> None:
    input_schema = EvidenceVerificationInput.model_json_schema()
    output_schema = EvidenceVerificationResult.model_json_schema()

    assert set(input_schema["required"]) == {"target", "evidence"}
    assert set(output_schema["required"]) == {
        "state",
        "reasons",
        "supporting_source_ids",
        "conflicting_source_ids",
        "explanation",
    }
    assert input_schema["additionalProperties"] is False
    assert output_schema["additionalProperties"] is False


def test_input_normalizes_identity_fields_but_preserves_evidence_text() -> None:
    item = EvidenceItem(
        source_id=" SOURCE_1 ",
        text="  Evidence with intentional surrounding whitespace.  ",
        source_type=" document ",
        source_ref=" chunk-1 ",
    )
    request = EvidenceVerificationInput(
        target=" Target ",
        evidence=(item,),
    )

    assert request.target == "Target"
    assert item.source_id == "SOURCE_1"
    assert item.source_type == "document"
    assert item.source_ref == "chunk-1"
    assert item.text == "  Evidence with intentional surrounding whitespace.  "


def test_validate_result_requires_no_evidence_state_for_empty_input() -> None:
    from app.answering.evidence_dto import EvidenceReason

    request = EvidenceVerificationInput(target="Target", evidence=())
    result = EvidenceVerificationResult(
        state=EvidenceState.INSUFFICIENT,
        reasons=(EvidenceReason.RELATION_MISSING,),
        supporting_source_ids=(),
        conflicting_source_ids=(),
        explanation="Invalid empty-evidence decision.",
    )

    with pytest.raises(
        ValueError,
        match="empty evidence request must return insufficient/no_evidence",
    ):
        validate_evidence_verification_result(request=request, result=result)


def test_result_rejects_multiple_insufficient_reasons() -> None:
    from app.answering.evidence_dto import EvidenceReason

    with pytest.raises(
        ValidationError,
        match="exactly one primary reason",
    ):
        EvidenceVerificationResult(
            state=EvidenceState.INSUFFICIENT,
            reasons=(
                EvidenceReason.SUBJECT_MISMATCH,
                EvidenceReason.ATTRIBUTE_MISSING,
            ),
            supporting_source_ids=(),
            conflicting_source_ids=(),
            explanation="Over-classified failure.",
        )
