from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from app.answering.chunk_repository import ChunkRepository
from app.api.dependencies import get_embedding_provider, get_vector_repository
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.retrieval.bm25_repository import BM25ChunkRepository
from app.retrieval.bm25_retrieval_service import BM25RetrievalService
from app.retrieval.dense_retrieval_service import DenseRetrievalService
from app.retrieval.hybrid_dto import HybridSearchHit
from app.retrieval.hybrid_retrieval_service import HybridRetrievalService
from app.retrieval.reranker import CrossEncoderRerankerProvider
from app.retrieval.reranking_service import RerankingService
from scripts.retrieval_eval import EvaluationCase, load_cases


MODEL_NAME = "BAAI/bge-reranker-v2-m3"


@dataclass
class SnapshotHybridService:
    hits: list[HybridSearchHit]

    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        candidate_limit: int = 20,
        limit: int = 5,
    ) -> list[HybridSearchHit]:
        del query, workspace_id, candidate_limit
        return self.hits[:limit]


def resolve_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def rank_of(
    *,
    expected_chunk_id: str,
    hits: list[HybridSearchHit],
) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if hit.chunk_id == expected_chunk_id:
            return rank
    return None


async def run(
    *,
    dataset_path: Path,
    case_index: int,
    candidate_limit: int,
    rerank_depth: int,
    top_k: int,
    rrf_k: int,
    batch_size: int,
    max_length: int,
) -> None:
    cases = load_cases(dataset_path)

    if not 1 <= case_index <= len(cases):
        raise ValueError(
            f"case_index must be between 1 and {len(cases)}"
        )

    case: EvaluationCase = cases[case_index - 1]
    device = resolve_device()

    dense_service = DenseRetrievalService(
        embedding_provider=get_embedding_provider(),
        vector_repository=get_vector_repository(),
    )

    async with AsyncSessionLocal() as session:
        bm25_service = BM25RetrievalService(
            chunk_repository=BM25ChunkRepository(session=session),
            k1=settings.bm25_k1,
            b=settings.bm25_b,
        )
        hybrid_service = HybridRetrievalService(
            dense_retrieval_service=dense_service,
            bm25_retrieval_service=bm25_service,
            rrf_k=rrf_k,
        )

        hybrid_started = time.perf_counter()
        hybrid_hits = await hybrid_service.search(
            query=case.query,
            workspace_id=case.workspace_id,
            candidate_limit=candidate_limit,
            limit=rerank_depth,
        )
        hybrid_ms = (time.perf_counter() - hybrid_started) * 1000

        provider = CrossEncoderRerankerProvider(
            model_name=MODEL_NAME,
            batch_size=batch_size,
            max_length=max_length,
            device=device,
        )
        reranking_service = RerankingService(
            hybrid_retrieval_service=SnapshotHybridService(hybrid_hits),
            chunk_repository=ChunkRepository(session=session),
            reranker_provider=provider,
        )

        cold_started = time.perf_counter()
        cold_hits = await reranking_service.search(
            query=case.query,
            workspace_id=case.workspace_id,
            candidate_limit=candidate_limit,
            rerank_depth=rerank_depth,
            limit=top_k,
        )
        cold_ms = (time.perf_counter() - cold_started) * 1000

        warm_started = time.perf_counter()
        warm_hits = await reranking_service.search(
            query=case.query,
            workspace_id=case.workspace_id,
            candidate_limit=candidate_limit,
            rerank_depth=rerank_depth,
            limit=top_k,
        )
        warm_ms = (time.perf_counter() - warm_started) * 1000

    original_rank = rank_of(
        expected_chunk_id=case.expected_chunk_id,
        hits=hybrid_hits,
    )
    reranked_rank = next(
        (
            hit.rerank_rank
            for hit in warm_hits
            if hit.hybrid_hit.chunk_id == case.expected_chunk_id
        ),
        None,
    )

    print("=" * 100)
    print("DAY 14 REAL RERANKER SMOKE")
    print("model:", MODEL_NAME)
    print("device:", device)
    print("case_index:", case_index)
    print("query:", case.query)
    print("expected_chunk_id:", case.expected_chunk_id)
    print("candidate_limit:", candidate_limit)
    print("rerank_depth:", rerank_depth)
    print("top_k:", top_k)
    print("hybrid_candidate_count:", len(hybrid_hits))
    print("expected_hybrid_rank:", original_rank)
    print("expected_reranked_rank_at_k:", reranked_rank)
    print(f"hybrid_latency_ms: {hybrid_ms:.2f}")
    print(f"rerank_cold_latency_ms: {cold_ms:.2f}")
    print(f"rerank_warm_latency_ms: {warm_ms:.2f}")
    print()
    print("RERANKED TOP-K")
    for hit in warm_hits:
        hybrid = hit.hybrid_hit
        print(
            f"  rerank={hit.rerank_rank:02d} "
            f"original={hit.original_rank:02d} "
            f"score={hit.reranker_score:.6f} "
            f"dense={hybrid.dense_rank} "
            f"bm25={hybrid.bm25_rank} "
            f"chunk={hybrid.chunk_id} "
            f"document={hybrid.document_name}"
        )
    print()
    print("RESULT: PASS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one real Hybrid + Cross Encoder reranker smoke case."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("eval/retrieval_golden.jsonl"),
    )
    parser.add_argument("--case-index", type=int, default=11)
    parser.add_argument("--candidate-limit", type=int, default=20)
    parser.add_argument("--rerank-depth", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=512)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(
        run(
            dataset_path=args.dataset,
            case_index=args.case_index,
            candidate_limit=args.candidate_limit,
            rerank_depth=args.rerank_depth,
            top_k=args.top_k,
            rrf_k=args.rrf_k,
            batch_size=args.batch_size,
            max_length=args.max_length,
        )
    )


if __name__ == "__main__":
    main()
