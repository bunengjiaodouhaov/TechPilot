from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


if __package__ in (None, ""):
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

from scripts.eval_contract import EvaluationContractError, sha256_file


SCRIPT_VERSION = "document-retrieval-matrix-run-v1"


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


def current_git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _word_shingles(text: str, size: int = 3) -> set[tuple[str, ...]]:
    tokens = [token.lower() for token in text.split() if token.strip()]
    if not tokens:
        return set()
    size = max(1, min(size, len(tokens)))
    return {
        tuple(tokens[index : index + size])
        for index in range(0, len(tokens) - size + 1)
    }


def _jaccard(left: set[Any], right: set[Any]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def mean_pairwise_redundancy(texts: list[str]) -> float:
    if len(texts) < 2:
        return 0.0
    shingles = [_word_shingles(text) for text in texts]
    values: list[float] = []
    for left in range(len(shingles)):
        for right in range(left + 1, len(shingles)):
            values.append(_jaccard(shingles[left], shingles[right]))
    return statistics.fmean(values) if values else 0.0


def _hit(
    *,
    chunk_db_id: int,
    chunk_id: str,
    document_id: int,
    document_name: str,
    score: float | None,
) -> dict[str, Any]:
    return {
        "chunk_db_id": int(chunk_db_id),
        "chunk_id": chunk_id,
        "document_id": int(document_id),
        "document_name": document_name,
        "score": score,
    }


async def run_matrix(
    *,
    truth_path: Path,
    output_dir: Path,
    candidate_limit: int,
    top_k_max: int,
    rrf_k: int,
    reranker_model: str | None,
    rerank_depth: int,
) -> dict[str, Any]:
    if candidate_limit < top_k_max:
        raise EvaluationContractError(
            "candidate_limit must be >= top_k_max"
        )
    if rerank_depth < top_k_max:
        raise EvaluationContractError(
            "rerank_depth must be >= top_k_max"
        )

    from sqlalchemy import select

    from app.answering.chunk_repository import ChunkRepository
    from app.api.dependencies import get_embedding_provider, get_vector_repository
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.models.chunk import Chunk
    from app.models.document import Document
    from app.retrieval.bm25_repository import BM25ChunkRepository
    from app.retrieval.bm25_retrieval_service import BM25RetrievalService
    from app.retrieval.dense_retrieval_service import DenseRetrievalService
    from app.retrieval.reranker import CrossEncoderRerankerProvider
    from app.retrieval.rrf import reciprocal_rank_fusion

    truth_rows = _load_jsonl(truth_path)
    workspace_ids = {
        int(row.get("workspace_id", 0))
        for row in truth_rows
        if row.get("workspace_id") is not None
    }
    if not workspace_ids:
        # Truth v1 stores workspace in summary, not row. Infer from DB documents
        # referenced by the truth map.
        expected_document_ids = {
            int(row["expected_document_id"]) for row in truth_rows
        }
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document.workspace_id)
                .where(Document.id.in_(expected_document_ids))
                .distinct()
            )
            workspace_ids = {int(value) for value in result.scalars()}
    if len(workspace_ids) != 1:
        raise EvaluationContractError(
            f"truth map must resolve to exactly one workspace: {workspace_ids}"
        )
    workspace_id = next(iter(workspace_ids))

    dense_service = DenseRetrievalService(
        embedding_provider=get_embedding_provider(),
        vector_repository=get_vector_repository(),
    )
    reranker = (
        CrossEncoderRerankerProvider(model_name=reranker_model)
        if reranker_model
        else None
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    run_path = output_dir / "document_retrieval_run_results.jsonl"
    temp_path = output_dir / "document_retrieval_run_results.partial.jsonl"

    rows_written = 0
    variants = ["dense", "bm25", "hybrid"]
    if reranker is not None:
        variants.append("hybrid_reranker")

    async with AsyncSessionLocal() as session:
        bm25_service = BM25RetrievalService(
            chunk_repository=BM25ChunkRepository(session=session),
            k1=settings.bm25_k1,
            b=settings.bm25_b,
        )

        chunk_result = await session.execute(
            select(Chunk, Document)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_(None),
            )
        )
        chunk_rows = list(chunk_result.all())
        chunk_text_by_db_id = {
            int(chunk.id): chunk.text for chunk, _ in chunk_rows
        }

        with temp_path.open("w", encoding="utf-8") as file:
            for index, truth in enumerate(truth_rows, 1):
                query = str(truth["query"])
                candidate_id = str(truth["candidate_id"])

                started = time.perf_counter()
                dense_hits = await dense_service.search(
                    query=query,
                    workspace_id=workspace_id,
                    limit=candidate_limit,
                )
                dense_ms = (time.perf_counter() - started) * 1000.0

                started = time.perf_counter()
                bm25_hits = await bm25_service.search(
                    query=query,
                    workspace_id=workspace_id,
                    limit=candidate_limit,
                )
                bm25_ms = (time.perf_counter() - started) * 1000.0

                dense_by_chunk = {}
                for hit in dense_hits:
                    dense_by_chunk.setdefault(hit.payload.chunk_id, hit)
                bm25_by_chunk = {}
                for hit in bm25_hits:
                    bm25_by_chunk.setdefault(hit.chunk_id, hit)

                started = time.perf_counter()
                fused = reciprocal_rank_fusion(
                    dense_chunk_ids=[
                        hit.payload.chunk_id for hit in dense_hits
                    ],
                    bm25_chunk_ids=[hit.chunk_id for hit in bm25_hits],
                    k=rrf_k,
                )
                fusion_ms = (time.perf_counter() - started) * 1000.0

                dense_serialized = [
                    _hit(
                        chunk_db_id=hit.point_id,
                        chunk_id=hit.payload.chunk_id,
                        document_id=hit.payload.document_id,
                        document_name=hit.payload.document_name,
                        score=float(hit.score),
                    )
                    for hit in dense_hits[:top_k_max]
                ]
                bm25_serialized = [
                    _hit(
                        chunk_db_id=hit.point_id,
                        chunk_id=hit.chunk_id,
                        document_id=hit.document_id,
                        document_name=hit.document_name,
                        score=float(hit.score),
                    )
                    for hit in bm25_hits[:top_k_max]
                ]

                hybrid_all: list[dict[str, Any]] = []
                for fused_hit in fused:
                    dense_hit = dense_by_chunk.get(fused_hit.chunk_id)
                    bm25_hit = bm25_by_chunk.get(fused_hit.chunk_id)
                    if dense_hit is not None:
                        hybrid_all.append(
                            _hit(
                                chunk_db_id=dense_hit.point_id,
                                chunk_id=dense_hit.payload.chunk_id,
                                document_id=dense_hit.payload.document_id,
                                document_name=dense_hit.payload.document_name,
                                score=float(fused_hit.score),
                            )
                        )
                    elif bm25_hit is not None:
                        hybrid_all.append(
                            _hit(
                                chunk_db_id=bm25_hit.point_id,
                                chunk_id=bm25_hit.chunk_id,
                                document_id=bm25_hit.document_id,
                                document_name=bm25_hit.document_name,
                                score=float(fused_hit.score),
                            )
                        )

                def redundancy(serialized: list[dict[str, Any]]) -> dict[str, float]:
                    values = {}
                    for k in (5, 10):
                        selected = serialized[: min(k, len(serialized))]
                        texts = [
                            chunk_text_by_db_id.get(int(item["chunk_db_id"]), "")
                            for item in selected
                        ]
                        values[str(k)] = mean_pairwise_redundancy(texts)
                    return values

                base_rows = [
                    {
                        "candidate_id": candidate_id,
                        "variant": "dense",
                        "latency_ms": dense_ms,
                        "hits": dense_serialized,
                        "redundancy_at_k": redundancy(dense_serialized),
                    },
                    {
                        "candidate_id": candidate_id,
                        "variant": "bm25",
                        "latency_ms": bm25_ms,
                        "hits": bm25_serialized,
                        "redundancy_at_k": redundancy(bm25_serialized),
                    },
                    {
                        "candidate_id": candidate_id,
                        "variant": "hybrid",
                        "latency_ms": dense_ms + bm25_ms + fusion_ms,
                        "hits": hybrid_all[:top_k_max],
                        "redundancy_at_k": redundancy(
                            hybrid_all[:top_k_max]
                        ),
                    },
                ]

                for row in base_rows:
                    file.write(json.dumps(row, ensure_ascii=False) + "\n")
                    rows_written += 1

                if reranker is not None:
                    rerank_candidates = hybrid_all[:rerank_depth]
                    texts = [
                        chunk_text_by_db_id[int(item["chunk_db_id"])]
                        for item in rerank_candidates
                    ]
                    started = time.perf_counter()
                    scores = await asyncio.to_thread(
                        reranker.score,
                        query=query,
                        documents=texts,
                    )
                    rerank_ms = (time.perf_counter() - started) * 1000.0
                    scored = list(zip(rerank_candidates, scores, strict=True))
                    scored.sort(
                        key=lambda item: (
                            -float(item[1]),
                            rerank_candidates.index(item[0]),
                            int(item[0]["chunk_db_id"]),
                        )
                    )
                    reranked = []
                    for item, score in scored[:top_k_max]:
                        copied = dict(item)
                        copied["score"] = float(score)
                        reranked.append(copied)

                    row = {
                        "candidate_id": candidate_id,
                        "variant": "hybrid_reranker",
                        "latency_ms": (
                            dense_ms + bm25_ms + fusion_ms + rerank_ms
                        ),
                        "hits": reranked,
                        "redundancy_at_k": redundancy(reranked),
                    }
                    file.write(json.dumps(row, ensure_ascii=False) + "\n")
                    rows_written += 1

                if index % 10 == 0 or index == len(truth_rows):
                    print(
                        f"[{index}/{len(truth_rows)}] "
                        f"dense={dense_ms:.1f}ms "
                        f"bm25={bm25_ms:.1f}ms "
                        f"variants={','.join(variants)}"
                    )

    temp_path.replace(run_path)
    summary = {
        "script_version": SCRIPT_VERSION,
        "git_sha": current_git_sha(),
        "truth_path": str(truth_path),
        "truth_sha256": sha256_file(truth_path),
        "workspace_id": workspace_id,
        "case_count": len(truth_rows),
        "variants": variants,
        "row_count": rows_written,
        "candidate_limit": candidate_limit,
        "top_k_max": top_k_max,
        "rrf_k": rrf_k,
        "reranker_model": reranker_model,
        "rerank_depth": rerank_depth if reranker_model else None,
        "bm25_k1": settings.bm25_k1,
        "bm25_b": settings.bm25_b,
        "embedding_model": settings.embedding_model,
        "run_path": str(run_path),
        "run_sha256": sha256_file(run_path),
    }
    summary_path = output_dir / "document_retrieval_run_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Dense/BM25/Hybrid/(optional) reranker on the frozen Document benchmark."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SCRIPT_VERSION}",
    )
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate-limit", type=int, default=40)
    parser.add_argument("--top-k-max", type=int, default=10)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--reranker-model")
    parser.add_argument("--rerank-depth", type=int, default=40)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = asyncio.run(
        run_matrix(
            truth_path=args.truth,
            output_dir=args.output_dir,
            candidate_limit=args.candidate_limit,
            top_k_max=args.top_k_max,
            rrf_k=args.rrf_k,
            reranker_model=args.reranker_model,
            rerank_depth=args.rerank_depth,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
