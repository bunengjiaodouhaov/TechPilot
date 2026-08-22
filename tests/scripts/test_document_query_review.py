from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.document_query_review import (
    ReviewDecision,
    freeze_accepted,
    load_decisions,
    prepare_review,
    review_status,
)
from scripts.eval_contract import EvaluationContractError


def _row(
    candidate_id: str,
    *,
    priority: str,
    category: str,
    document: str,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "request_id": f"r-{candidate_id}",
        "anchor_id": f"a-{candidate_id}",
        "document_key": document,
        "topic": "topic",
        "page": 1,
        "section": None,
        "source_unit_sha256": "a" * 64,
        "category": category,
        "variant": "primary",
        "query": f"What requirement applies to {candidate_id}?",
        "answer_text": "A concise answer.",
        "evidence_quote": "A sufficiently long evidence quote supporting the answer.",
        "generation_mode": "llm_batch",
        "generator_model": "fake",
        "batch_id": "b",
        "repair_count": 0,
        "curation": {
            "quality_score": 100,
            "flags": [],
            "review_priority": priority,
            "exact_duplicate_of": None,
            "same_anchor_near_duplicate_of": None,
            "cross_document_near_duplicate_ids": [],
        },
    }


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_prepare_separates_low_risk_and_review_queue(tmp_path: Path) -> None:
    shortlist = tmp_path / "shortlist.jsonl"
    rows = [
        _row("c1", priority="low", category="direct_fact", document="d1"),
        _row("c2", priority="medium", category="semantic_paraphrase", document="d1"),
        _row("c3", priority="high", category="section_concept", document="d2"),
        _row("c4", priority="low", category="keyword_identifier", document="d2"),
    ]
    _write(shortlist, rows)
    out = tmp_path / "review"
    summary = prepare_review(
        shortlist_path=shortlist,
        output_dir=out,
        final_target=3,
        packet_size=1,
    )
    assert summary["auto_accept_count"] == 2
    assert summary["review_case_count"] == 2
    assert summary["packet_count"] == 2


def test_status_counts_accept_reject_and_pending(tmp_path: Path) -> None:
    shortlist = tmp_path / "shortlist.jsonl"
    rows = [
        _row("c1", priority="low", category="direct_fact", document="d1"),
        _row("c2", priority="medium", category="semantic_paraphrase", document="d1"),
        _row("c3", priority="high", category="section_concept", document="d2"),
    ]
    _write(shortlist, rows)
    out = tmp_path / "review"
    prepare_review(
        shortlist_path=shortlist,
        output_dir=out,
        final_target=2,
        packet_size=10,
    )
    decisions = [
        {
            "candidate_id": "c2",
            "decision": "accept",
            "reviewer": "v",
            "reviewed_at": "2026-08-21T00:00:00+00:00",
            "edited_query": None,
            "reason": None,
        }
    ]
    _write(out / "review_decisions.jsonl", decisions)
    status = review_status(review_dir=out)
    assert status["accepted_total_count"] == 2
    assert status["pending_review_count"] == 1
    assert status["target_reached"] is True


def test_freeze_requires_enough_accepted_cases(tmp_path: Path) -> None:
    shortlist = tmp_path / "shortlist.jsonl"
    rows = [
        _row("c1", priority="low", category="direct_fact", document="d1"),
        _row("c2", priority="medium", category="semantic_paraphrase", document="d1"),
        _row("c3", priority="medium", category="semantic_paraphrase", document="d2"),
    ]
    _write(shortlist, rows)
    out = tmp_path / "review"
    prepare_review(
        shortlist_path=shortlist,
        output_dir=out,
        final_target=3,
        packet_size=10,
    )
    with pytest.raises(EvaluationContractError, match="below final target"):
        freeze_accepted(
            review_dir=out,
            output_path=tmp_path / "final.jsonl",
        )


def test_edit_decision_requires_question(tmp_path: Path) -> None:
    with pytest.raises(EvaluationContractError, match="must end with"):
        ReviewDecision(
            candidate_id="c1",
            decision="edit",
            reviewer="v",
            reviewed_at="now",
            edited_query="not a question",
        ).validate()
