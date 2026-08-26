from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.chunk import Chunk
from app.models.document import Document
from app.retrieval.hybrid_dto import HybridSearchHit
from app.retrieval.reranker import CrossEncoderRerankerProvider
from scripts.docx_semantic_gap_eval import (
    DEFAULT_DATASET,
    _aggregate,
    _build_hybrid,
    _case_metrics,
    _rerank_candidates,
    _single_query_strategy,
    load_cases,
)


DEFAULT_OUTPUT = Path(".local/p6/docx_semantic_gap_structural_eval.json")
DEFAULT_RERANKER = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


@dataclass(frozen=True)
class StructuralCandidate:
    hit: HybridSearchHit
    reason: str
    support_count: int
    best_anchor_rank: int
    distance: int


def _parent_section(section: str | None) -> str | None:
    if section is None:
        return None
    normalized = section.strip()
    if not normalized or " > " not in normalized:
        return None
    parent = normalized.rsplit(" > ", 1)[0].strip()
    return parent or None


def _to_hit(*, chunk: Chunk, document: Document) -> HybridSearchHit:
    return HybridSearchHit(
        point_id=chunk.id,
        rrf_score=0.0,
        workspace_id=document.workspace_id,
        document_id=document.id,
        chunk_id=chunk.chunk_id,
        chunk_index=chunk.chunk_index,
        section=chunk.section,
        document_name=document.name,
        source_type=document.file_type,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        dense_rank=None,
        dense_score=None,
        bm25_rank=None,
        bm25_score=None,
    )


async def _load_parent_siblings(
    *,
    session: AsyncSession,
    workspace_id: int,
    document_id: int,
    parent: str,
) -> list[tuple[Chunk, Document]]:
    statement = (
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(
            Document.workspace_id == workspace_id,
            Document.id == document_id,
            Document.deleted_at.is_(None),
            or_(
                Chunk.section == parent,
                Chunk.section.startswith(parent + " > "),
            ),
        )
        .order_by(Chunk.chunk_index.asc())
    )
    result = await session.execute(statement)
    return list(result.all())


async def _load_neighbors(
    *,
    session: AsyncSession,
    workspace_id: int,
    document_id: int,
    center: int,
    radius: int,
) -> list[tuple[Chunk, Document]]:
    statement = (
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(
            Document.workspace_id == workspace_id,
            Document.id == document_id,
            Document.deleted_at.is_(None),
            Chunk.chunk_index >= max(0, center - radius),
            Chunk.chunk_index <= center + radius,
        )
        .order_by(Chunk.chunk_index.asc())
    )
    result = await session.execute(statement)
    return list(result.all())


async def _structural_expand(
    *,
    session: AsyncSession,
    workspace_id: int,
    anchors: Sequence[HybridSearchHit],
    anchor_limit: int,
    max_additions: int,
    neighbor_radius: int,
) -> tuple[list[HybridSearchHit], dict[int, str]]:
    selected_anchors = list(anchors[:anchor_limit])
    anchor_ids = {hit.point_id for hit in anchors}

    parent_groups: dict[tuple[int, str], list[tuple[int, HybridSearchHit]]] = {}
    fallback_anchors: list[tuple[int, HybridSearchHit]] = []

    for rank, anchor in enumerate(selected_anchors, start=1):
        parent = _parent_section(anchor.section)
        if parent is None:
            fallback_anchors.append((rank, anchor))
            continue
        parent_groups.setdefault((anchor.document_id, parent), []).append(
            (rank, anchor)
        )

    pool: dict[int, StructuralCandidate] = {}

    # Prefer parent sections independently supported by multiple top anchors.
    for (document_id, parent), group_anchors in parent_groups.items():
        rows = await _load_parent_siblings(
            session=session,
            workspace_id=workspace_id,
            document_id=document_id,
            parent=parent,
        )
        support_count = len(group_anchors)
        best_anchor_rank = min(rank for rank, _ in group_anchors)

        for chunk, document in rows:
            if chunk.id in anchor_ids:
                continue
            distance = min(
                abs(chunk.chunk_index - anchor.chunk_index)
                for _, anchor in group_anchors
            )
            candidate = StructuralCandidate(
                hit=_to_hit(chunk=chunk, document=document),
                reason=f"parent_section:{parent}",
                support_count=support_count,
                best_anchor_rank=best_anchor_rank,
                distance=distance,
            )
            previous = pool.get(chunk.id)
            if previous is None or (
                -candidate.support_count,
                candidate.best_anchor_rank,
                candidate.distance,
                candidate.hit.chunk_index,
            ) < (
                -previous.support_count,
                previous.best_anchor_rank,
                previous.distance,
                previous.hit.chunk_index,
            ):
                pool[chunk.id] = candidate

    # Only use index neighbors when the retrieved anchor has no usable hierarchy.
    for rank, anchor in fallback_anchors:
        rows = await _load_neighbors(
            session=session,
            workspace_id=workspace_id,
            document_id=anchor.document_id,
            center=anchor.chunk_index,
            radius=neighbor_radius,
        )
        for chunk, document in rows:
            if chunk.id in anchor_ids:
                continue
            candidate = StructuralCandidate(
                hit=_to_hit(chunk=chunk, document=document),
                reason=f"neighbor:{anchor.chunk_index}±{neighbor_radius}",
                support_count=0,
                best_anchor_rank=rank,
                distance=abs(chunk.chunk_index - anchor.chunk_index),
            )
            previous = pool.get(chunk.id)
            if previous is None:
                pool[chunk.id] = candidate

    ordered = sorted(
        pool.values(),
        key=lambda item: (
            0 if item.support_count > 0 else 1,
            -item.support_count,
            item.best_anchor_rank,
            item.distance,
            item.hit.document_id,
            item.hit.chunk_index,
            item.hit.point_id,
        ),
    )
    additions = ordered[:max_additions]
    return (
        [item.hit for item in additions],
        {item.hit.point_id: item.reason for item in additions},
    )


async def _structural_strategy(
    *,
    session: AsyncSession,
    hybrid,
    provider: CrossEncoderRerankerProvider,
    query: str,
    workspace_id: int,
    candidate_limit: int,
    rerank_depth: int,
    anchor_limit: int,
    max_additions: int,
    neighbor_radius: int,
    final_limit: int,
) -> tuple[list[HybridSearchHit], list[Any], dict[int, str]]:
    anchors = await hybrid.search(
        query=query,
        workspace_id=workspace_id,
        candidate_limit=candidate_limit,
        limit=rerank_depth,
    )
    additions, reasons = await _structural_expand(
        session=session,
        workspace_id=workspace_id,
        anchors=anchors,
        anchor_limit=anchor_limit,
        max_additions=max_additions,
        neighbor_radius=neighbor_radius,
    )
    candidates = [*anchors, *additions]
    ranked = await _rerank_candidates(
        session=session,
        provider=provider,
        candidates=candidates,
        query_by_point={hit.point_id: (query,) for hit in candidates},
        fallback_query=query,
        limit=final_limit,
    )
    return candidates, ranked, reasons


async def _warmup(
    *,
    session: AsyncSession,
    hybrid,
    provider: CrossEncoderRerankerProvider,
    query: str,
    workspace_id: int,
    candidate_limit: int,
    rerank_depth: int,
) -> None:
    candidates = await hybrid.search(
        query=query,
        workspace_id=workspace_id,
        candidate_limit=candidate_limit,
        limit=min(rerank_depth, 5),
    )
    if candidates:
        await _rerank_candidates(
            session=session,
            provider=provider,
            candidates=candidates,
            query_by_point={hit.point_id: (query,) for hit in candidates},
            fallback_query=query,
            limit=min(5, len(candidates)),
        )


async def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(args.dataset)
    provider = CrossEncoderRerankerProvider(
        model_name=args.reranker_model,
        batch_size=settings.reranker_batch_size,
        max_length=settings.reranker_max_length,
    )

    results: dict[str, list[dict[str, Any]]] = {
        "baseline": [],
        "structural": [],
    }

    async with AsyncSessionLocal() as session:
        hybrid = _build_hybrid(session)

        if not args.no_warmup:
            print("[warmup] loading retrieval/reranker providers; excluded from metrics")
            await _warmup(
                session=session,
                hybrid=hybrid,
                provider=provider,
                query=cases[0].question,
                workspace_id=args.workspace_id,
                candidate_limit=args.candidate_limit,
                rerank_depth=args.rerank_depth,
            )

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
            structural_candidates, structural_ranked, reasons = (
                await _structural_strategy(
                    session=session,
                    hybrid=hybrid,
                    provider=provider,
                    query=case.question,
                    workspace_id=args.workspace_id,
                    candidate_limit=args.candidate_limit,
                    rerank_depth=args.rerank_depth,
                    anchor_limit=args.anchor_limit,
                    max_additions=args.max_additions,
                    neighbor_radius=args.neighbor_radius,
                    final_limit=args.final_limit,
                )
            )
            structural_row = _case_metrics(
                case=case,
                candidates=structural_candidates,
                ranked=structural_ranked,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )
            structural_row["structural_additions"] = [
                {
                    "point_id": hit.point_id,
                    "chunk_index": hit.chunk_index,
                    "section": hit.section,
                    "reason": reasons[hit.point_id],
                    "gold": (
                        case.document_name_contains.lower()
                        in hit.document_name.lower()
                        and hit.chunk_index in set(case.gold_chunk_indices)
                    ),
                }
                for hit in structural_candidates[len(baseline_candidates):]
            ]
            results["structural"].append(structural_row)

    return {
        "config": {
            "dataset": str(args.dataset),
            "workspace_id": args.workspace_id,
            "reranker_model": args.reranker_model,
            "candidate_limit": args.candidate_limit,
            "rerank_depth": args.rerank_depth,
            "anchor_limit": args.anchor_limit,
            "max_additions": args.max_additions,
            "neighbor_radius": args.neighbor_radius,
            "final_limit": args.final_limit,
            "embedding_model": settings.embedding_model,
            "rrf_k": settings.answer_rrf_k,
            "warmup_excluded": not args.no_warmup,
        },
        "summary": {
            name: _aggregate(rows)
            for name, rows in results.items()
        },
        "cases": results,
        "interpretation_guardrails": [
            "This is a curated DOCX semantic-gap regression set, not a public benchmark.",
            "Structural expansion is evaluation-only and does not modify the production answer path.",
            "Expansion uses only document structure already stored in PostgreSQL: document_id, section and chunk_index.",
            "Hierarchical sections are expanded by parent heading; chunks without hierarchy fall back to bounded index neighbors.",
            "All expanded candidates are reranked with the original user query; no LLM query generation is used.",
            "A warmup pass is excluded from latency by default so baseline and structural timings share loaded providers.",
        ],
    }


def print_summary(report: dict[str, Any]) -> None:
    print("\n=== STRUCTURAL EXPANSION SUMMARY ===")
    for name, metrics in report["summary"].items():
        latency = metrics["latency_ms"]
        print(
            f"{name:12s}"
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
            "Compare multilingual baseline retrieval with bounded structural "
            "candidate expansion on the EnterpriseOps DOCX semantic-gap set."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--reranker-model", default=DEFAULT_RERANKER)
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
    parser.add_argument("--anchor-limit", type=int, default=12)
    parser.add_argument("--max-additions", type=int, default=12)
    parser.add_argument("--neighbor-radius", type=int, default=2)
    parser.add_argument("--final-limit", type=int, default=20)
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    for field in (
        "workspace_id",
        "candidate_limit",
        "rerank_depth",
        "anchor_limit",
        "max_additions",
        "neighbor_radius",
        "final_limit",
    ):
        if getattr(args, field) <= 0:
            parser.error(f"--{field.replace('_', '-')} must be positive")

    if args.final_limit > args.rerank_depth + args.max_additions:
        parser.error(
            "--final-limit must not exceed rerank-depth + max-additions"
        )
    if args.rerank_depth > args.candidate_limit * 2:
        parser.error(
            "--rerank-depth must not exceed twice --candidate-limit"
        )
    if args.anchor_limit > args.rerank_depth:
        parser.error("--anchor-limit must not exceed --rerank-depth")

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
