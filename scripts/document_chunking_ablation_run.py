from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


if __package__ in (None, ""):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

from scripts.document_benchmark_ingest import ingest_corpus
from scripts.document_chunking_compare import compare
from scripts.document_retrieval_matrix_run import run_matrix
from scripts.document_retrieval_metrics import run_cli
from scripts.document_retrieval_truth_project import build_truth_map
from scripts.eval_contract import EvaluationContractError


SCRIPT_VERSION = "document-chunking-ablation-run-v1"
DEFAULT_CONFIGS = (
    "c1200_o150:1200:150",
    "c1000_o120:1000:120",
    "c800_o100:800:100",
)


def parse_config(value: str) -> tuple[str, int, int]:
    parts = value.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "config must be LABEL:MAX_CHARS:OVERLAP_CHARS"
        )
    label = parts[0].strip()
    if not label:
        raise argparse.ArgumentTypeError("config label must not be empty")
    try:
        max_chars = int(parts[1])
        overlap_chars = int(parts[2])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "MAX_CHARS and OVERLAP_CHARS must be integers"
        ) from exc
    if max_chars < 100:
        raise argparse.ArgumentTypeError("MAX_CHARS must be >= 100")
    if not 0 <= overlap_chars < max_chars:
        raise argparse.ArgumentTypeError(
            "OVERLAP_CHARS must be in [0, MAX_CHARS)"
        )
    return label, max_chars, overlap_chars


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise EvaluationContractError(f"expected JSON object: {path}")
    return payload


async def run_config(
    *,
    label: str,
    max_chars: int,
    overlap_chars: int,
    dataset: Path,
    corpus_root: Path,
    output_root: Path,
    workspace_prefix: str,
    candidate_limit: int,
    top_k_max: int,
    rrf_k: int,
    resume: bool,
) -> dict[str, Any]:
    config_root = output_root / label
    ingest_dir = config_root / "ingestion"
    truth_dir = config_root / "truth"
    matrix_dir = config_root / "matrix"
    metrics_dir = config_root / "metrics"

    metrics_summary_path = (
        metrics_dir / "document_retrieval_metrics_summary.json"
    )
    if resume and metrics_summary_path.exists():
        print(f"[{label}] resume: metrics already complete")
        return {
            "label": label,
            "max_chars": max_chars,
            "overlap_chars": overlap_chars,
            "ingestion_summary": str(
                ingest_dir / "benchmark_ingestion_summary.json"
            ),
            "truth_summary": str(
                truth_dir / "document_retrieval_truth_summary.json"
            ),
            "metrics_summary": str(metrics_summary_path),
            "resumed": True,
        }

    workspace_name = f"{workspace_prefix}-{label}"

    print()
    print("=" * 100)
    print(
        f"[{label}] chunk_max_chars={max_chars} "
        f"overlap_chars={overlap_chars}"
    )

    ingestion = await ingest_corpus(
        corpus_root=corpus_root,
        workspace_name=workspace_name,
        output_dir=ingest_dir,
        chunk_max_chars=max_chars,
        chunk_overlap_chars=overlap_chars,
    )
    if not ingestion["complete"]:
        raise EvaluationContractError(
            f"{label}: benchmark ingestion did not complete"
        )

    truth = await build_truth_map(
        dataset_path=dataset,
        corpus_root=corpus_root,
        workspace_id=int(ingestion["workspace_id"]),
        output_dir=truth_dir,
        minimum_projection_coverage=0.80,
    )
    if not truth["complete"] or truth["projected_count"] != truth["case_count"]:
        raise EvaluationContractError(
            f"{label}: truth projection gate failed: "
            f"{truth['projected_count']}/{truth['case_count']}"
        )

    run_summary = await run_matrix(
        truth_path=Path(truth["truth_map_path"]),
        output_dir=matrix_dir,
        candidate_limit=candidate_limit,
        top_k_max=top_k_max,
        rrf_k=rrf_k,
        reranker_model=None,
        rerank_depth=max(candidate_limit, top_k_max),
    )

    metrics = run_cli(
        truth_path=Path(truth["truth_map_path"]),
        run_path=Path(run_summary["run_path"]),
        output_dir=metrics_dir,
        ks=(1, 3, 5, 10),
    )

    overall = metrics["matrix"]["hybrid"]["overall"]
    print(
        f"[{label}] Hybrid "
        f"EvidenceCoverage@5={overall['evidence_coverage_at_5']:.4f} "
        f"EvidenceHit@5={overall['evidence_hit_at_5']:.4f} "
        f"Recall@5={overall['recall_at_5']:.4f} "
        f"MRR@5={overall['mrr_at_5']:.4f} "
        f"nDCG@5={overall['ndcg_at_5']:.4f}"
    )

    return {
        "label": label,
        "max_chars": max_chars,
        "overlap_chars": overlap_chars,
        "workspace_id": ingestion["workspace_id"],
        "total_chunks": ingestion.get("total_chunks"),
        "ingestion_summary": str(
            ingest_dir / "benchmark_ingestion_summary.json"
        ),
        "truth_summary": str(
            truth_dir / "document_retrieval_truth_summary.json"
        ),
        "metrics_summary": str(metrics_summary_path),
        "resumed": False,
    }


async def run_all(args: argparse.Namespace) -> dict[str, Any]:
    parsed_configs = [
        parse_config(value)
        for value in (args.config or DEFAULT_CONFIGS)
    ]

    seen_labels: set[str] = set()
    for label, _, _ in parsed_configs:
        if label in seen_labels:
            raise EvaluationContractError(
                f"duplicate chunking config label: {label}"
            )
        seen_labels.add(label)

    args.output_root.mkdir(parents=True, exist_ok=True)

    completed: list[dict[str, Any]] = []
    for label, max_chars, overlap_chars in parsed_configs:
        completed.append(
            await run_config(
                label=label,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
                dataset=args.dataset,
                corpus_root=args.corpus_root,
                output_root=args.output_root,
                workspace_prefix=args.workspace_prefix,
                candidate_limit=args.candidate_limit,
                top_k_max=args.top_k_max,
                rrf_k=args.rrf_k,
                resume=args.resume,
            )
        )

    compare_specs = [f"baseline_1200_o0:{args.baseline_metrics}"]
    if args.baseline_ingestion is not None:
        compare_specs = [
            f"baseline_1200_o0:{args.baseline_metrics}:{args.baseline_ingestion}"
        ]

    for item in completed:
        compare_specs.append(
            f"{item['label']}:{item['metrics_summary']}:{item['ingestion_summary']}"
        )

    comparison = compare(compare_specs)
    comparison_path = args.output_root / "chunking_ablation_comparison.json"
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    run_summary = {
        "script_version": SCRIPT_VERSION,
        "dataset": str(args.dataset),
        "corpus_root": str(args.corpus_root),
        "baseline_metrics": str(args.baseline_metrics),
        "configs": completed,
        "comparison_path": str(comparison_path),
        "winner": comparison["winner"],
        "selection_policy": comparison["selection_policy"],
    }
    summary_path = args.output_root / "chunking_ablation_run_summary.json"
    summary_path.write_text(
        json.dumps(run_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 100)
    print("CHUNKING ABLATION RESULT")
    for rank, row in enumerate(comparison["ranked"], 1):
        print(
            f"{rank}. {row['label']:<18} "
            f"Coverage@5={row['evidence_coverage_at_5']:.4f} "
            f"EvHit@5={row['evidence_hit_at_5']:.4f} "
            f"Recall@5={row['recall_at_5']:.4f} "
            f"MRR@5={row['mrr_at_5']:.4f} "
            f"nDCG@5={row['ndcg_at_5']:.4f} "
            f"P95={row['latency_p95_ms']:.1f}ms "
            f"Redundancy@5={row['redundancy_at_5']}"
        )
    print("winner:", comparison["winner"])
    print("comparison:", comparison_path)
    return run_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run controlled Document chunking ablation on the frozen benchmark."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SCRIPT_VERSION}",
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--baseline-metrics", type=Path, required=True)
    parser.add_argument("--baseline-ingestion", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--workspace-prefix",
        default="eval-document-400-chunk",
    )
    parser.add_argument(
        "--config",
        action="append",
        help=(
            "LABEL:MAX_CHARS:OVERLAP_CHARS; repeat for multiple configs. "
            "Defaults: c1200_o150, c1000_o120, c800_o100."
        ),
    )
    parser.add_argument("--candidate-limit", type=int, default=40)
    parser.add_argument("--top-k-max", type=int, default=10)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip a config when its metrics summary already exists.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run_all(args))


if __name__ == "__main__":
    main()
