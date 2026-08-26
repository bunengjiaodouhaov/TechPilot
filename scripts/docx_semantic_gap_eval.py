from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.ext.asyncio import AsyncSession

from app.answering.chunk_repository import ChunkRepository
from app.api.dependencies import get_dense_retrieval_service
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.retrieval.bm25_repository import BM25ChunkRepository
from app.retrieval.bm25_retrieval_service import BM25RetrievalService
from app.retrieval.hybrid_dto import HybridSearchHit
from app.retrieval.hybrid_retrieval_service import HybridRetrievalService
from app.retrieval.reranker import CrossEncoderRerankerProvider


DEFAULT_DATASET = Path("evals/retrieval/docx_semantic_gap_cases.jsonl")
DEFAULT_OUTPUT = Path(".local/p6/docx_semantic_gap_eval.json")


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    question: str
    flat_expansion: str
    subqueries: tuple[str, ...]
    gold_chunk_indices: tuple[int, ...]
    document_name_contains: str
    notes: str | None = None


@dataclass(frozen=True)
class RankedCandidate:
    hit: HybridSearchHit
    score: float
    source_queries: tuple[str, ...]


def load_cases(path: Path) -> list[EvalCase]:
    rows: list[EvalCase] = []

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue

        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"expected object at {path}:{line_number}")

        case_id = str(payload.get("case_id", "")).strip()
        question = str(payload.get("question", "")).strip()
        flat_expansion = str(payload.get("flat_expansion", "")).strip()
        document_name_contains = str(
            payload.get("document_name_contains", "")
        ).strip()
        subqueries_raw = payload.get("subqueries")
        gold_raw = payload.get("gold_chunk_indices")

        if not case_id or not question or not flat_expansion:
            raise ValueError(
                "case_id/question/flat_expansion required at "
                f"{path}:{line_number}"
            )
        if not document_name_contains:
            raise ValueError(
                f"document_name_contains required at {path}:{line_number}"
            )
        if not isinstance(subqueries_raw, list) or not subqueries_raw:
            raise ValueError(
                f"non-empty subqueries required at {path}:{line_number}"
            )
        subqueries = tuple(
            str(value).strip()
            for value in subqueries_raw
            if str(value).strip()
        )
        if not subqueries:
            raise ValueError(
                f"non-empty subqueries required at {path}:{line_number}"
            )
        if not isinstance(gold_raw, list) or not gold_raw:
            raise ValueError(
                f"non-empty gold_chunk_indices required at {path}:{line_number}"
            )
        gold = tuple(int(value) for value in gold_raw)
        if any(value < 0 for value in gold):
            raise ValueError(
                "gold_chunk_indices must be non-negative at "
                f"{path}:{line_number}"
            )

        notes_value = payload.get("notes")
        notes = (
            str(notes_value).strip()
            if notes_value is not None and str(notes_value).strip()
            else None
        )

        rows.append(
            EvalCase(
                case_id=case_id,
                question=question,
                flat_expansion=flat_expansion,
                subqueries=subqueries,
                gold_chunk_indices=gold,
                document_name_contains=document_name_contains,
                notes=notes,
            )
        )

    if not rows:
        raise ValueError(f"dataset is empty: {path}")

    case_ids = [case.case_id for case in rows]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case_id values must be unique")

    return rows


def _gold_match(case: EvalCase, hit: HybridSearchHit) -> bool:
    return (
        case.document_name_contains.lower() in hit.document_name.lower()
        and hit.chunk_index in set(case.gold_chunk_indices)
    )


def _recall_at_k(
    *,
    case: EvalCase,
    ranked_hits: Sequence[HybridSearchHit],
    k: int,
) -> float:
    gold = set(case.gold_chunk_indices)
    found = {
        hit.chunk_index
        for hit in ranked_hits[:k]
        if case.document_name_contains.lower() in hit.document_name.lower()
        and hit.chunk_index in gold
    }
    return len(found) / len(gold)


def _mrr_at_k(
    *,
    case: EvalCase,
    ranked_hits: Sequence[HybridSearchHit],
    k: int,
) -> float:
    for rank, hit in enumerate(ranked_hits[:k], start=1):
        if _gold_match(case, hit):
            return 1.0 / rank
    return 0.0


def _any_hit_at_k(
    *,
    case: EvalCase,
    ranked_hits: Sequence[HybridSearchHit],
    k: int,
) -> bool:
    return any(_gold_match(case, hit) for hit in ranked_hits[:k])


def _p95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def _round_robin_union(
    rankings: Sequence[tuple[str, Sequence[HybridSearchHit]]],
    *,
    limit: int,
) -> tuple[list[HybridSearchHit], dict[int, tuple[str, ...]]]:
    if limit <= 0:
        raise ValueError("limit must be positive")

    selected: list[HybridSearchHit] = []
    selected_ids: set[int] = set()
    max_depth = max((len(hits) for _, hits in rankings), default=0)

    for depth in range(max_depth):
        for _, hits in rankings:
            if depth >= len(hits):
                continue
            hit = hits[depth]
            if hit.point_id in selected_ids:
                continue
            selected_ids.add(hit.point_id)
            selected.append(hit)
            if len(selected) >= limit:
                break
        if len(selected) >= limit:
            break

    sources: dict[int, list[str]] = {
        point_id: []
        for point_id in selected_ids
    }
    for query, hits in rankings:
        for hit in hits:
            if hit.point_id not in selected_ids:
                continue
            if query not in sources[hit.point_id]:
                sources[hit.point_id].append(query)

    return selected, {
        point_id: tuple(query_list)
        for point_id, query_list in sources.items()
    }


def _build_hybrid(session: AsyncSession) -> HybridRetrievalService:
    bm25 = BM25RetrievalService(
        chunk_repository=BM25ChunkRepository(session=session),
        k1=settings.bm25_k1,
        b=settings.bm25_b,
    )
    return HybridRetrievalService(
        dense_retrieval_service=get_dense_retrieval_service(),
        bm25_retrieval_service=bm25,
        rrf_k=settings.answer_rrf_k,
    )


async def _rerank_candidates(
    *,
    session: AsyncSession,
    provider: CrossEncoderRerankerProvider,
    candidates: Sequence[HybridSearchHit],
    query_by_point: dict[int, tuple[str, ...]],
    fallback_query: str,
    limit: int,
) -> list[RankedCandidate]:
    if not candidates:
        return []

    workspace_id = candidates[0].workspace_id
    if any(hit.workspace_id != workspace_id for hit in candidates):
        raise ValueError("candidate workspace mismatch")

    repo = ChunkRepository(session=session)
    chunks = await repo.get_by_ids(
        chunk_ids=[hit.point_id for hit in candidates],
        workspace_id=workspace_id,
    )

    missing = [
        hit.point_id
        for hit in candidates
        if hit.point_id not in chunks
    ]
    if missing:
        raise RuntimeError(
            "candidate chunks missing from PostgreSQL: "
            + ", ".join(str(value) for value in missing)
        )

    # End the PostgreSQL read transaction before local model inference.
    await session.commit()

    source_queries = {
        hit.point_id: (
            query_by_point.get(hit.point_id)
            or (fallback_query,)
        )
        for hit in candidates
    }

    query_to_points: dict[str, list[int]] = {}
    for point_id, queries in source_queries.items():
        for query in queries:
            query_to_points.setdefault(query, []).append(point_id)

    best_scores: dict[int, float] = {
        hit.point_id: float("-inf")
        for hit in candidates
    }

    for query, point_ids in query_to_points.items():
        documents = [chunks[point_id].text for point_id in point_ids]
        scores = await asyncio.to_thread(
            provider.score,
            query=query,
            documents=documents,
        )
        if len(scores) != len(point_ids):
            raise RuntimeError("reranker score count mismatch")
        for point_id, score in zip(point_ids, scores, strict=True):
            best_scores[point_id] = max(
                best_scores[point_id],
                float(score),
            )

    original_order = {
        hit.point_id: rank
        for rank, hit in enumerate(candidates, start=1)
    }
    ranked = [
        RankedCandidate(
            hit=hit,
            score=best_scores[hit.point_id],
            source_queries=source_queries[hit.point_id],
        )
        for hit in candidates
    ]
    ranked.sort(
        key=lambda item: (
            -item.score,
            original_order[item.hit.point_id],
            item.hit.chunk_id,
        )
    )
    return ranked[:limit]


async def _single_query_strategy(
    *,
    session: AsyncSession,
    hybrid: HybridRetrievalService,
    provider: CrossEncoderRerankerProvider,
    query: str,
    workspace_id: int,
    candidate_limit: int,
    rerank_depth: int,
    final_limit: int,
) -> tuple[list[HybridSearchHit], list[RankedCandidate]]:
    candidates = await hybrid.search(
        query=query,
        workspace_id=workspace_id,
        candidate_limit=candidate_limit,
        limit=rerank_depth,
    )
    ranked = await _rerank_candidates(
        session=session,
        provider=provider,
        candidates=candidates,
        query_by_point={hit.point_id: (query,) for hit in candidates},
        fallback_query=query,
        limit=final_limit,
    )
    return candidates, ranked


async def _multi_query_strategy(
    *,
    session: AsyncSession,
    hybrid: HybridRetrievalService,
    provider: CrossEncoderRerankerProvider,
    case: EvalCase,
    workspace_id: int,
    candidate_limit: int,
    rerank_depth: int,
    per_query_limit: int,
    final_limit: int,
) -> tuple[list[HybridSearchHit], list[RankedCandidate]]:
    queries = (case.question, *case.subqueries)
    rankings: list[tuple[str, list[HybridSearchHit]]] = []

    for query in queries:
        hits = await hybrid.search(
            query=query,
            workspace_id=workspace_id,
            candidate_limit=candidate_limit,
            limit=per_query_limit,
        )
        rankings.append((query, hits))

    candidates, query_by_point = _round_robin_union(
        rankings,
        limit=rerank_depth,
    )
    ranked = await _rerank_candidates(
        session=session,
        provider=provider,
        candidates=candidates,
        query_by_point=query_by_point,
        fallback_query=case.question,
        limit=final_limit,
    )
    return candidates, ranked


def _case_metrics(
    *,
    case: EvalCase,
    candidates: Sequence[HybridSearchHit],
    ranked: Sequence[RankedCandidate],
    elapsed_ms: float,
) -> dict[str, Any]:
    final_hits = [item.hit for item in ranked]

    return {
        "case_id": case.case_id,
        "question": case.question,
        "gold_chunk_indices": list(case.gold_chunk_indices),
        "candidate_recall_at_depth": _recall_at_k(
            case=case,
            ranked_hits=candidates,
            k=len(candidates),
        ),
        "recall_at_5": _recall_at_k(
            case=case,
            ranked_hits=final_hits,
            k=5,
        ),
        "recall_at_20": _recall_at_k(
            case=case,
            ranked_hits=final_hits,
            k=20,
        ),
        "mrr_at_5": _mrr_at_k(
            case=case,
            ranked_hits=final_hits,
            k=5,
        ),
        "mrr_at_20": _mrr_at_k(
            case=case,
            ranked_hits=final_hits,
            k=20,
        ),
        "any_hit_at_5": _any_hit_at_k(
            case=case,
            ranked_hits=final_hits,
            k=5,
        ),
        "any_hit_at_20": _any_hit_at_k(
            case=case,
            ranked_hits=final_hits,
            k=20,
        ),
        "elapsed_ms": elapsed_ms,
        "top_20": [
            {
                "rank": rank,
                "point_id": item.hit.point_id,
                "chunk_index": item.hit.chunk_index,
                "document_name": item.hit.document_name,
                "section": item.hit.section,
                "score": item.score,
                "gold": _gold_match(case, item.hit),
                "source_queries": list(item.source_queries),
            }
            for rank, item in enumerate(ranked[:20], start=1)
        ],
    }


def _aggregate(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("cannot aggregate empty results")

    elapsed = [float(row["elapsed_ms"]) for row in rows]
    return {
        "cases": len(rows),
        "candidate_recall_at_depth": statistics.fmean(
            float(row["candidate_recall_at_depth"])
            for row in rows
        ),
        "recall_at_5": statistics.fmean(
            float(row["recall_at_5"])
            for row in rows
        ),
        "recall_at_20": statistics.fmean(
            float(row["recall_at_20"])
            for row in rows
        ),
        "mrr_at_5": statistics.fmean(
            float(row["mrr_at_5"])
            for row in rows
        ),
        "mrr_at_20": statistics.fmean(
            float(row["mrr_at_20"])
            for row in rows
        ),
        "any_hit_at_5": statistics.fmean(
            1.0 if row["any_hit_at_5"] else 0.0
            for row in rows
        ),
        "any_hit_at_20": statistics.fmean(
            1.0 if row["any_hit_at_20"] else 0.0
            for row in rows
        ),
        "latency_ms": {
            "mean": statistics.fmean(elapsed),
            "p95": _p95(elapsed),
        },
    }


async def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(args.dataset)
    provider = CrossEncoderRerankerProvider(
        model_name=args.reranker_model,
        batch_size=settings.reranker_batch_size,
        max_length=settings.reranker_max_length,
    )

    results: dict[str, list[dict[str, Any]]] = {
        "baseline": [],
        "flat_expansion": [],
        "multi_query": [],
    }

    async with AsyncSessionLocal() as session:
        hybrid = _build_hybrid(session)

        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case.case_id}")

            started = time.perf_counter()
            baseline_candidates, baseline_ranked = await _single_query_strategy(
                session=session,
                hybrid=hybrid,
                provider=provider,
                query=case.question,
                workspace_id=args.workspace_id,
                candidate_limit=args.candidate_limit,
                rerank_depth=args.rerank_depth,
                final_limit=args.final_limit,
            )
            results["baseline"].append(
                _case_metrics(
                    case=case,
                    candidates=baseline_candidates,
                    ranked=baseline_ranked,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                )
            )

            started = time.perf_counter()
            flat_candidates, flat_ranked = await _single_query_strategy(
                session=session,
                hybrid=hybrid,
                provider=provider,
                query=case.flat_expansion,
                workspace_id=args.workspace_id,
                candidate_limit=args.candidate_limit,
                rerank_depth=args.rerank_depth,
                final_limit=args.final_limit,
            )
            results["flat_expansion"].append(
                _case_metrics(
                    case=case,
                    candidates=flat_candidates,
                    ranked=flat_ranked,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                )
            )

            started = time.perf_counter()
            multi_candidates, multi_ranked = await _multi_query_strategy(
                session=session,
                hybrid=hybrid,
                provider=provider,
                case=case,
                workspace_id=args.workspace_id,
                candidate_limit=args.candidate_limit,
                rerank_depth=args.rerank_depth,
                per_query_limit=args.per_query_limit,
                final_limit=args.final_limit,
            )
            results["multi_query"].append(
                _case_metrics(
                    case=case,
                    candidates=multi_candidates,
                    ranked=multi_ranked,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                )
            )

    return {
        "config": {
            "dataset": str(args.dataset),
            "workspace_id": args.workspace_id,
            "reranker_model": args.reranker_model,
            "candidate_limit": args.candidate_limit,
            "rerank_depth": args.rerank_depth,
            "per_query_limit": args.per_query_limit,
            "final_limit": args.final_limit,
            "embedding_model": settings.embedding_model,
            "rrf_k": settings.answer_rrf_k,
        },
        "summary": {
            name: _aggregate(rows)
            for name, rows in results.items()
        },
        "cases": results,
        "interpretation_guardrails": [
            "This is a curated DOCX semantic-gap regression set, not a public benchmark.",
            "Gold labels use stable chunk_index values for the EnterpriseOps document, not database point IDs.",
            "Flat expansions and subqueries are manually curated retrieval probes; they are not evidence of an automatic query-rewrite model.",
            "Multi-query preserves subquery diversity with round-robin candidate union before reranking.",
            "The original user question remains the answer target; expanded queries are retrieval-only.",
        ],
    }


def print_summary(report: dict[str, Any]) -> None:
    print("\n=== SUMMARY ===")
    for name, metrics in report["summary"].items():
        latency = metrics["latency_ms"]
        print(
            f"{name:16s}"
            f" Recall@5={metrics['recall_at_5']:.4f}"
            f" Recall@20={metrics['recall_at_20']:.4f}"
            f" MRR@5={metrics['mrr_at_5']:.4f}"
            f" MRR@20={metrics['mrr_at_20']:.4f}"
            f" Any@5={metrics['any_hit_at_5']:.4f}"
            f" CandidateRecall={metrics['candidate_recall_at_depth']:.4f}"
            f" mean={latency['mean']:.1f}ms"
            f" p95={latency['p95']:.1f}ms"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare baseline, flat query expansion, and bounded multi-query "
            "retrieval on the curated EnterpriseOps DOCX semantic-gap set."
        )
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
    )
    parser.add_argument(
        "--workspace-id",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--reranker-model",
        default=settings.reranker_model,
    )
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=settings.answer_retrieval_candidate_limit,
    )
    parser.add_argument(
        "--rerank-depth",
        type=int,
        default=settings.answer_rerank_depth,
    )
    parser.add_argument(
        "--per-query-limit",
        type=int,
        default=8,
    )
    parser.add_argument(
        "--final-limit",
        type=int,
        default=20,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()

    for field in (
        "workspace_id",
        "candidate_limit",
        "rerank_depth",
        "per_query_limit",
        "final_limit",
    ):
        if getattr(args, field) <= 0:
            parser.error(f"--{field.replace('_', '-')} must be positive")

    if args.final_limit > args.rerank_depth:
        parser.error("--final-limit must not exceed --rerank-depth")
    if args.rerank_depth > args.candidate_limit * 2:
        parser.error(
            "--rerank-depth must not exceed twice --candidate-limit"
        )
    if args.per_query_limit > args.candidate_limit * 2:
        parser.error(
            "--per-query-limit must not exceed twice --candidate-limit"
        )

    return args


def main() -> None:
    args = parse_args()
    report = asyncio.run(run_eval(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print_summary(report)
    print(f"\nreport: {args.output}")


if __name__ == "__main__":
    main()
