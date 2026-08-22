from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


if __package__ in (None, ""):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

from scripts.eval_contract import EvaluationContractError, sha256_file


SCRIPT_VERSION = "document-retrieval-metrics-v1"
DEFAULT_KS = (1, 3, 5, 10)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise EvaluationContractError(
                f"expected object at {path}:{line_number}"
            )
        rows.append(row)
    if not rows:
        raise EvaluationContractError(f"empty JSONL: {path}")
    return rows


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def reciprocal_rank(
    *,
    ranked_ids: list[int],
    relevant_ids: set[int],
    k: int,
) -> float:
    for rank, chunk_id in enumerate(ranked_ids[:k], 1):
        if chunk_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def dcg(relevances: list[int], k: int) -> float:
    return sum(
        (2**relevance - 1) / math.log2(rank + 1)
        for rank, relevance in enumerate(relevances[:k], 1)
    )


def ndcg_at_k(
    *,
    ranked_ids: list[int],
    relevance_by_id: dict[int, int],
    k: int,
) -> float:
    actual = [relevance_by_id.get(chunk_id, 0) for chunk_id in ranked_ids]
    ideal = sorted(relevance_by_id.values(), reverse=True)
    ideal_dcg = dcg(ideal, k)
    if ideal_dcg == 0:
        return 0.0
    return dcg(actual, k) / ideal_dcg


def evidence_coverage_at_k(
    *,
    ranked_ids: list[int],
    evidence_shingle_count: int,
    shingle_indices_by_id: dict[int, set[int]],
    k: int,
) -> float:
    if evidence_shingle_count <= 0:
        return 0.0
    covered: set[int] = set()
    for chunk_id in ranked_ids[:k]:
        covered.update(shingle_indices_by_id.get(chunk_id, set()))
    return min(1.0, len(covered) / evidence_shingle_count)


def score_case(
    *,
    truth: dict[str, Any],
    run: dict[str, Any],
    ks: tuple[int, ...] = DEFAULT_KS,
) -> dict[str, Any]:
    relevant_chunks = truth.get("relevant_chunks")
    if not isinstance(relevant_chunks, list) or not relevant_chunks:
        raise EvaluationContractError(
            f"truth case {truth.get('candidate_id')} has no relevant chunks"
        )
    hits = run.get("hits")
    if not isinstance(hits, list):
        raise EvaluationContractError(
            f"run case {run.get('candidate_id')} hits must be a list"
        )

    ranked_ids = [int(hit["chunk_db_id"]) for hit in hits]
    hit_document_ids = [int(hit["document_id"]) for hit in hits]

    relevance_by_id = {
        int(item["chunk_db_id"]): int(item["relevance_grade"])
        for item in relevant_chunks
    }
    relevant_ids = set(relevance_by_id)
    shingle_indices_by_id = {
        int(item["chunk_db_id"]): {
            int(index) for index in item.get("evidence_shingle_indices", [])
        }
        for item in relevant_chunks
    }
    expected_document_id = int(truth["expected_document_id"])
    evidence_shingle_count = int(truth["evidence_shingle_count"])

    metrics: dict[str, Any] = {}
    for k in ks:
        retrieved_relevant = [
            chunk_id for chunk_id in ranked_ids[:k] if chunk_id in relevant_ids
        ]
        metrics[f"document_hit_at_{k}"] = float(
            expected_document_id in hit_document_ids[:k]
        )
        metrics[f"evidence_hit_at_{k}"] = float(bool(retrieved_relevant))
        metrics[f"recall_at_{k}"] = (
            len(set(retrieved_relevant)) / len(relevant_ids)
        )
        metrics[f"precision_at_{k}"] = (
            len(set(retrieved_relevant)) / k
        )
        metrics[f"mrr_at_{k}"] = reciprocal_rank(
            ranked_ids=ranked_ids,
            relevant_ids=relevant_ids,
            k=k,
        )
        metrics[f"ndcg_at_{k}"] = ndcg_at_k(
            ranked_ids=ranked_ids,
            relevance_by_id=relevance_by_id,
            k=k,
        )
        metrics[f"evidence_coverage_at_{k}"] = evidence_coverage_at_k(
            ranked_ids=ranked_ids,
            evidence_shingle_count=evidence_shingle_count,
            shingle_indices_by_id=shingle_indices_by_id,
            k=k,
        )

    return {
        "candidate_id": truth["candidate_id"],
        "variant": run["variant"],
        "category": truth["category"],
        "document_key": truth["document_key"],
        "latency_ms": float(run.get("latency_ms", 0.0)),
        "redundancy_at_k": run.get("redundancy_at_k", {}),
        "metrics": metrics,
    }


def _aggregate_case_rows(
    rows: list[dict[str, Any]],
    *,
    ks: tuple[int, ...],
) -> dict[str, Any]:
    if not rows:
        raise EvaluationContractError("cannot aggregate empty case rows")
    metric_names = [
        f"{name}_at_{k}"
        for k in ks
        for name in (
            "document_hit",
            "evidence_hit",
            "recall",
            "precision",
            "mrr",
            "ndcg",
            "evidence_coverage",
        )
    ]
    summary = {
        name: sum(row["metrics"][name] for row in rows) / len(rows)
        for name in metric_names
    }

    latencies = [float(row["latency_ms"]) for row in rows]
    summary["latency_ms"] = {
        "mean": statistics.fmean(latencies),
        "p50": _percentile(latencies, 0.50),
        "p95": _percentile(latencies, 0.95),
        "p99": _percentile(latencies, 0.99),
        "max": max(latencies),
    }

    redundancy_by_k: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        redundancy = row.get("redundancy_at_k") or {}
        if not isinstance(redundancy, dict):
            continue
        for key, value in redundancy.items():
            redundancy_by_k[str(key)].append(float(value))
    summary["redundancy_at_k"] = {
        key: statistics.fmean(values)
        for key, values in sorted(redundancy_by_k.items())
        if values
    }
    return summary


def score_matrix(
    *,
    truth_rows: list[dict[str, Any]],
    run_rows: list[dict[str, Any]],
    ks: tuple[int, ...] = DEFAULT_KS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    truth_by_id = {str(row["candidate_id"]): row for row in truth_rows}
    if len(truth_by_id) != len(truth_rows):
        raise EvaluationContractError("duplicate candidate_id in truth map")

    by_variant: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in run_rows:
        variant = str(row.get("variant", "")).strip()
        candidate_id = str(row.get("candidate_id", "")).strip()
        if not variant or not candidate_id:
            raise EvaluationContractError("run row missing variant/candidate_id")
        if candidate_id in by_variant[variant]:
            raise EvaluationContractError(
                f"duplicate run row: variant={variant} candidate_id={candidate_id}"
            )
        by_variant[variant][candidate_id] = row

    case_rows: list[dict[str, Any]] = []
    matrix: dict[str, Any] = {}

    for variant, variant_rows in sorted(by_variant.items()):
        missing = sorted(set(truth_by_id) - set(variant_rows))
        extra = sorted(set(variant_rows) - set(truth_by_id))
        if missing or extra:
            raise EvaluationContractError(
                f"variant {variant} truth/run mismatch: "
                f"missing={len(missing)} extra={len(extra)}"
            )

        scored = [
            score_case(
                truth=truth_by_id[candidate_id],
                run=variant_rows[candidate_id],
                ks=ks,
            )
            for candidate_id in truth_by_id
        ]
        case_rows.extend(scored)

        by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_document: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in scored:
            by_category[row["category"]].append(row)
            by_document[row["document_key"]].append(row)

        matrix[variant] = {
            "cases": len(scored),
            "overall": _aggregate_case_rows(scored, ks=ks),
            "by_category": {
                category: _aggregate_case_rows(rows, ks=ks)
                for category, rows in sorted(by_category.items())
            },
            "by_document": {
                document: _aggregate_case_rows(rows, ks=ks)
                for document, rows in sorted(by_document.items())
            },
        }

    return matrix, case_rows


def run_cli(
    *,
    truth_path: Path,
    run_path: Path,
    output_dir: Path,
    ks: tuple[int, ...],
) -> dict[str, Any]:
    truth_rows = _load_jsonl(truth_path)
    run_rows = _load_jsonl(run_path)
    matrix, case_rows = score_matrix(
        truth_rows=truth_rows,
        run_rows=run_rows,
        ks=ks,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    case_path = output_dir / "document_retrieval_case_metrics.jsonl"
    with case_path.open("w", encoding="utf-8") as file:
        for row in case_rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "script_version": SCRIPT_VERSION,
        "truth_path": str(truth_path),
        "truth_sha256": sha256_file(truth_path),
        "run_path": str(run_path),
        "run_sha256": sha256_file(run_path),
        "ks": list(ks),
        "variants": sorted(matrix),
        "matrix": matrix,
        "case_metrics_path": str(case_path),
    }
    summary_path = output_dir / "document_retrieval_metrics_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score standardized Document retrieval runs."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SCRIPT_VERSION}",
    )
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ks", default="1,3,5,10")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ks = tuple(sorted({int(value) for value in args.ks.split(",") if value}))
    if not ks or any(value <= 0 for value in ks):
        raise EvaluationContractError("ks must contain positive integers")
    summary = run_cli(
        truth_path=args.truth,
        run_path=args.run,
        output_dir=args.output_dir,
        ks=ks,
    )
    print(
        json.dumps(
            {
                "script_version": summary["script_version"],
                "variants": summary["variants"],
                "ks": summary["ks"],
                "summary_path": str(
                    args.output_dir / "document_retrieval_metrics_summary.json"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
