from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.document_query_candidates import (
    QueryCandidate,
    QueryGenerationRequest,
    validate_candidates_against_requests,
)


def _request() -> QueryGenerationRequest:
    text = "Organizations should maintain tested incident response plans and define clear roles before an incident occurs."
    return QueryGenerationRequest(
        request_id="qreq-0001-p",
        anchor_id="a1",
        document_key="doc1",
        topic="incident-response",
        page=4,
        section="Preparation",
        source_unit_sha256=hashlib.sha256(
            ("full " + text).encode("utf-8")
        ).hexdigest(),
        evidence_text=text,
        requested_category="direct_fact",
        variant="primary",
    )


def test_request_candidate_contract_accepts_grounded_candidate() -> None:
    request = _request()
    quote = "maintain tested incident response plans"
    candidate = QueryCandidate(
        candidate_id="c1",
        request_id=request.request_id,
        anchor_id=request.anchor_id,
        document_key=request.document_key,
        topic=request.topic,
        page=request.page,
        section=request.section,
        source_unit_sha256=request.source_unit_sha256,
        category=request.requested_category,
        variant=request.variant,
        query="What should organizations maintain before an incident occurs?",
        answer_text="Tested incident response plans.",
        evidence_quote=quote,
        generation_mode="llm_batch",
        generator_model="fake",
        batch_id="b1",
        repair_count=0,
    )
    assert validate_candidates_against_requests(
        requests=[request], candidates=[candidate]
    ) == []


def test_request_candidate_contract_rejects_non_source_quote() -> None:
    request = _request()
    candidate = QueryCandidate(
        candidate_id="c1",
        request_id=request.request_id,
        anchor_id=request.anchor_id,
        document_key=request.document_key,
        topic=request.topic,
        page=request.page,
        section=request.section,
        source_unit_sha256=request.source_unit_sha256,
        category=request.requested_category,
        variant=request.variant,
        query="What should organizations maintain before an incident occurs?",
        answer_text="Tested incident response plans.",
        evidence_quote="this phrase is not present in the source",
        generation_mode="llm_batch",
        generator_model="fake",
        batch_id="b1",
        repair_count=0,
    )
    errors = validate_candidates_against_requests(
        requests=[request], candidates=[candidate]
    )
    assert any("exact substring" in item for item in errors)


def test_whitespace_normalized_quote_matches_pdf_line_breaks() -> None:
    from scripts.document_query_candidates import evidence_quote_matches_source

    source = "design specifications and stakeholder expecta -\n tions. These products"
    quote = "design specifications and stakeholder expecta - tions. These products"
    assert evidence_quote_matches_source(quote=quote, source_text=source)


def test_whitespace_normalized_quote_still_rejects_changed_words() -> None:
    from scripts.document_query_candidates import evidence_quote_matches_source

    source = "Configuration management plans are generated during development."
    quote = "Configuration management plans are generated after deployment."
    assert not evidence_quote_matches_source(quote=quote, source_text=source)


def test_pdf_whitespace_normalization_accepts_line_break_difference() -> None:
    from scripts.document_query_candidates import evidence_quote_matches_source

    assert evidence_quote_matches_source(
        quote="stakeholder expecta - tions",
        source_text="stakeholder expecta -\n tions",
    )
