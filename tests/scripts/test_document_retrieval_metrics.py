from __future__ import annotations

import pytest

from scripts.document_retrieval_metrics import (
    evidence_coverage_at_k,
    ndcg_at_k,
    reciprocal_rank,
    score_case,
    score_matrix,
)


def _truth(candidate_id: str, category: str = "direct_fact") -> dict:
    return {
        "candidate_id": candidate_id,
        "query": "What is required?",
        "category": category,
        "document_key": "doc-a",
        "expected_document_id": 10,
        "evidence_shingle_count": 10,
        "relevant_chunks": [
            {
                "chunk_db_id": 101,
                "chunk_id": "c101",
                "relevance_grade": 3,
                "evidence_shingle_indices": [0, 1, 2, 3, 4, 5],
            },
            {
                "chunk_db_id": 102,
                "chunk_id": "c102",
                "relevance_grade": 1,
                "evidence_shingle_indices": [5, 6, 7, 8, 9],
            },
        ],
    }


def _run(candidate_id: str, variant: str = "dense") -> dict:
    return {
        "candidate_id": candidate_id,
        "variant": variant,
        "latency_ms": 12.0,
        "redundancy_at_k": {"5": 0.2, "10": 0.25},
        "hits": [
            {
                "chunk_db_id": 999,
                "chunk_id": "x",
                "document_id": 99,
                "document_name": "other",
            },
            {
                "chunk_db_id": 101,
                "chunk_id": "c101",
                "document_id": 10,
                "document_name": "gold",
            },
            {
                "chunk_db_id": 102,
                "chunk_id": "c102",
                "document_id": 10,
                "document_name": "gold",
            },
        ],
    }


def test_core_metrics() -> None:
    assert reciprocal_rank(
        ranked_ids=[9, 101, 102],
        relevant_ids={101, 102},
        k=3,
    ) == 0.5
    assert ndcg_at_k(
        ranked_ids=[9, 101, 102],
        relevance_by_id={101: 3, 102: 1},
        k=3,
    ) < 1.0
    assert evidence_coverage_at_k(
        ranked_ids=[9, 101, 102],
        evidence_shingle_count=10,
        shingle_indices_by_id={
            101: {0, 1, 2, 3, 4, 5},
            102: {5, 6, 7, 8, 9},
        },
        k=3,
    ) == 1.0


def test_score_case_reports_recall_mrr_and_document_hit() -> None:
    scored = score_case(
        truth=_truth("q1"),
        run=_run("q1"),
        ks=(1, 3),
    )
    assert scored["metrics"]["document_hit_at_1"] == 0.0
    assert scored["metrics"]["document_hit_at_3"] == 1.0
    assert scored["metrics"]["evidence_hit_at_3"] == 1.0
    assert scored["metrics"]["recall_at_3"] == 1.0
    assert scored["metrics"]["mrr_at_3"] == 0.5
    assert scored["metrics"]["evidence_coverage_at_3"] == 1.0


def test_score_matrix_splits_categories() -> None:
    truths = [
        _truth("q1", "direct_fact"),
        _truth("q2", "semantic_paraphrase"),
    ]
    runs = [
        _run("q1", "dense"),
        _run("q2", "dense"),
        _run("q1", "hybrid"),
        _run("q2", "hybrid"),
    ]
    matrix, case_rows = score_matrix(
        truth_rows=truths,
        run_rows=runs,
        ks=(1, 3),
    )
    assert sorted(matrix) == ["dense", "hybrid"]
    assert matrix["dense"]["cases"] == 2
    assert set(matrix["dense"]["by_category"]) == {
        "direct_fact",
        "semantic_paraphrase",
    }
    assert len(case_rows) == 4
