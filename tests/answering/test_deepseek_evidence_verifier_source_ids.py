from __future__ import annotations

import json

import httpx
import pytest

from app.answering.deepseek_evidence_verifier import (
    DeepSeekEvidenceVerifierError,
    DeepSeekEvidenceVerifierProvider,
)
from app.answering.evidence_dto import (
    EvidenceItem,
    EvidenceVerificationInput,
)


def _request(
    *,
    source_id: str = "SOURCE_1",
    source_ref: str = "chunk-hash-1",
) -> EvidenceVerificationInput:
    return EvidenceVerificationInput(
        target="What does the evidence support?",
        evidence=(
            EvidenceItem(
                source_id=source_id,
                source_ref=source_ref,
                source_type="document_chunk",
                title="doc.md",
                locator=None,
                text="The supplied evidence directly supports the target.",
            ),
        ),
    )


def _response(*, supporting_source_id: str) -> httpx.Response:
    content = json.dumps(
        {
            "state": "sufficient",
            "reasons": [],
            "supporting_source_ids": [supporting_source_id],
            "conflicting_source_ids": [],
            "explanation": "The supplied evidence supports the target.",
        }
    )
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": content,
                    }
                }
            ]
        },
    )


def test_parse_response_keeps_valid_source_id() -> None:
    request = _request()

    result = DeepSeekEvidenceVerifierProvider._parse_response(
        response=_response(supporting_source_id="SOURCE_1"),
        request=request,
    )

    assert result.supporting_source_ids == ("SOURCE_1",)


def test_parse_response_normalizes_unique_source_ref() -> None:
    request = _request(
        source_id="SOURCE_1",
        source_ref="abc123",
    )

    result = DeepSeekEvidenceVerifierProvider._parse_response(
        response=_response(supporting_source_id="abc123"),
        request=request,
    )

    assert result.supporting_source_ids == ("SOURCE_1",)


def test_parse_response_still_rejects_unknown_identifier() -> None:
    request = _request()

    with pytest.raises(
        DeepSeekEvidenceVerifierError,
        match="referenced unknown sources",
    ):
        DeepSeekEvidenceVerifierProvider._parse_response(
            response=_response(
                supporting_source_id="completely-invented-id",
            ),
            request=request,
        )


def test_ambiguous_source_ref_is_not_normalized() -> None:
    request = EvidenceVerificationInput(
        target="What does the evidence support?",
        evidence=(
            EvidenceItem(
                source_id="SOURCE_1",
                source_ref="same-ref",
                source_type="document_chunk",
                text="Evidence one.",
            ),
            EvidenceItem(
                source_id="SOURCE_2",
                source_ref="same-ref",
                source_type="document_chunk",
                text="Evidence two.",
            ),
        ),
    )

    with pytest.raises(
        DeepSeekEvidenceVerifierError,
        match="referenced unknown sources",
    ):
        DeepSeekEvidenceVerifierProvider._parse_response(
            response=_response(
                supporting_source_id="same-ref",
            ),
            request=request,
        )
