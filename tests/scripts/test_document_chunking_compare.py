from __future__ import annotations

import json
from pathlib import Path

from scripts.document_chunking_compare import compare


def _write_metrics(path: Path, *, coverage: float, hit: float) -> None:
    payload = {
        "matrix": {
            "hybrid": {
                "overall": {
                    "document_hit_at_5": 0.95,
                    "evidence_hit_at_5": hit,
                    "recall_at_5": 0.75,
                    "mrr_at_5": 0.61,
                    "ndcg_at_5": 0.64,
                    "evidence_coverage_at_5": coverage,
                    "redundancy_at_k": {"5": 0.10},
                    "latency_ms": {
                        "p50": 500.0,
                        "p95": 700.0,
                    },
                }
            }
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_compare_prefers_evidence_coverage_over_recall(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _write_metrics(first, coverage=0.80, hit=0.82)
    _write_metrics(second, coverage=0.85, hit=0.80)

    result = compare(
        [
            f"baseline:{first}",
            f"candidate:{second}",
        ]
    )
    assert result["winner"] == "candidate"
