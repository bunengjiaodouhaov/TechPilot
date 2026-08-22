from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "document-chunking-compare-v1"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _row(
    *,
    label: str,
    metrics_path: Path,
    ingestion_path: Path | None,
) -> dict[str, Any]:
    metrics = _load(metrics_path)
    hybrid = metrics["matrix"]["hybrid"]["overall"]

    chunking = None
    total_chunks = None
    if ingestion_path is not None:
        ingestion = _load(ingestion_path)
        chunking = ingestion.get("chunking")
        total_chunks = ingestion.get("total_chunks")

    return {
        "label": label,
        "chunking": chunking,
        "total_chunks": total_chunks,
        "document_hit_at_5": hybrid["document_hit_at_5"],
        "evidence_hit_at_5": hybrid["evidence_hit_at_5"],
        "recall_at_5": hybrid["recall_at_5"],
        "mrr_at_5": hybrid["mrr_at_5"],
        "ndcg_at_5": hybrid["ndcg_at_5"],
        "evidence_coverage_at_5": hybrid["evidence_coverage_at_5"],
        "redundancy_at_5": hybrid.get("redundancy_at_k", {}).get("5"),
        "latency_p50_ms": hybrid["latency_ms"]["p50"],
        "latency_p95_ms": hybrid["latency_ms"]["p95"],
    }


def compare(configs: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in configs:
        parts = item.split(":", 2)
        if len(parts) not in {2, 3}:
            raise ValueError(
                "--config must be LABEL:METRICS[:INGESTION]"
            )
        label = parts[0]
        metrics_path = Path(parts[1])
        ingestion_path = Path(parts[2]) if len(parts) == 3 else None
        rows.append(
            _row(
                label=label,
                metrics_path=metrics_path,
                ingestion_path=ingestion_path,
            )
        )

    # Cross-chunking primary objective is evidence span coverage, not Recall@5:
    # changing chunk size changes the number of relevant chunks and therefore
    # changes the Recall denominator.
    ranked = sorted(
        rows,
        key=lambda row: (
            -row["evidence_coverage_at_5"],
            -row["evidence_hit_at_5"],
            -row["ndcg_at_5"],
            -row["mrr_at_5"],
            row["latency_p95_ms"],
        ),
    )
    return {
        "script_version": SCRIPT_VERSION,
        "selection_policy": (
            "evidence_coverage_at_5 desc, evidence_hit_at_5 desc, "
            "ndcg_at_5 desc, mrr_at_5 desc, latency_p95_ms asc"
        ),
        "note": (
            "Recall@5 is reported but is not the primary cross-chunking selector "
            "because the relevant-chunk denominator changes with chunk boundaries."
        ),
        "winner": ranked[0]["label"] if ranked else None,
        "ranked": ranked,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SCRIPT_VERSION}",
    )
    parser.add_argument(
        "--config",
        action="append",
        required=True,
        help="LABEL:METRICS_JSON[:INGESTION_SUMMARY_JSON]",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = compare(args.config)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
