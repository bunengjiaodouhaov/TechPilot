from __future__ import annotations

import json
from pathlib import Path

from scripts.answer_evidence_metrics import score


def test_metrics_use_projected_chunk_truth(tmp_path: Path) -> None:
    truth = tmp_path / "truth.jsonl"
    truth.write_text(
        json.dumps(
            {
                "candidate_id": "c1",
                "expected_document_name": "doc.pdf",
                "evidence_shingle_count": 4,
                "relevant_chunks": [
                    {
                        "chunk_id": "a",
                        "evidence_shingle_indices": [0, 1, 2],
                    },
                    {
                        "chunk_id": "b",
                        "evidence_shingle_indices": [2, 3],
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    results = tmp_path / "results.jsonl"
    results.write_text(
        json.dumps(
            {
                "case": {
                    "id": "c1",
                    "category": "direct_fact",
                    "reference_answer": "alpha beta",
                },
                "actual": {
                    "answer_text": "alpha beta",
                    "refused": False,
                    "error": None,
                    "citations": [
                        {
                            "chunk_id": "a",
                            "document_name": "doc.pdf",
                        }
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = score(
        results_path=results,
        truth_path=truth,
    )
    overall = payload["overall"]
    assert overall["citation_hit_rate"] == 1.0
    assert overall["citation_precision"] == 1.0
    assert overall["citation_recall"] == 0.5
    assert overall["evidence_coverage"] == 0.75
