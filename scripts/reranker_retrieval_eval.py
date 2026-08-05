from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import subprocess
import time
from pathlib import Path

from app.answering.chunk_repository import ChunkRepository
from app.api.dependencies import get_embedding_provider, get_vector_repository
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.retrieval.bm25_repository import BM25ChunkRepository
from app.retrieval.bm25_retrieval_service import BM25RetrievalService
from app.retrieval.dense_retrieval_service import DenseRetrievalService
from app.retrieval.reranker import CrossEncoderRerankerProvider
from app.retrieval.rrf import reciprocal_rank_fusion
from scripts.hybrid_retrieval_eval import (
    corpus_snapshot_sha256,
    current_git_sha,
    file_sha256,
    load_legal_corpus,
    metrics_from_ranks,
    rank_of,
    validate_golden_integrity,
)
from scripts.retrieval_eval import EvaluationCase, load_cases


EXPECTED_CASES = 30
EXPECTED_LEGAL_DOCUMENTS = 5
EXPECTED_LEGAL_CHUNKS = 1153
EXPECTED_DATASET_SHA256 = (
    "e65e8490ef8e23018673712b2e595c6779e842094bd49d5d433fc5641bcef7f5"
)
EXPECTED_CORPUS_SHA256 = (
    "1d393523789b235bcfc1f821491bf86c5bcd29f47e04da7ebb85362b9ad81b0e"
)


def git_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return True
    return bool(result.stdout.strip())


def rank_at_k(
    *,
    expected_chunk_id: str,
    chunk_ids: list[str],
    top_k: int,
) -> int | None:
    rank = rank_of(
        expected_chunk_id=expected_chunk_id,
        chunk_ids=chunk_ids,
    )
    if rank is None or rank > top_k:
        return None
    return rank


def resolve_device(requested: str) -> str:
    normalized = requested.strip().lower()
    if normalized != "auto":
        return normalized

    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def percentile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("cannot compute percentile of empty values")

    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower = math.floor(position)
    upper = math.ceil(position)

    if lower == upper:
        return ordered[lower]

    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        raise ValueError("cannot summarize empty latency values")

    return {
        "mean_ms": statistics.mean(values),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "min_ms": min(values),
        "max_ms": max(values),
    }


def source_point_id(
    *,
    chunk_id: str,
    dense_by_chunk_id: dict,
    bm25_by_chunk_id: dict,
) -> int:
    dense_hit = dense_by_chunk_id.get(chunk_id)
    if dense_hit is not None:
        return dense_hit.point_id

    bm25_hit = bm25_by_chunk_id.get(chunk_id)
    if bm25_hit is not None:
        return bm25_hit.point_id

    raise RuntimeError(
        f"RRF returned chunk without a source hit: chunk_id={chunk_id}"
    )


async def rerank_same_snapshot(
    *,
    query: str,
    workspace_id: int,
    fused_results: list,
    dense_by_chunk_id: dict,
    bm25_by_chunk_id: dict,
    chunk_repository: ChunkRepository,
    reranker_provider: CrossEncoderRerankerProvider,
    rerank_depth: int,
    top_k: int,
) -> tuple[list[dict], float, float, float]:
    selected = fused_results[:rerank_depth]

    point_ids = [
        source_point_id(
            chunk_id=result.chunk_id,
            dense_by_chunk_id=dense_by_chunk_id,
            bm25_by_chunk_id=bm25_by_chunk_id,
        )
        for result in selected
    ]

    fetch_started = time.perf_counter()
    stored_by_id = await chunk_repository.get_by_ids(
        chunk_ids=point_ids,
        workspace_id=workspace_id,
    )
    fetch_ms = (time.perf_counter() - fetch_started) * 1000.0

    stored_chunks = []
    for original_rank, (result, point_id) in enumerate(
        zip(selected, point_ids, strict=True),
        start=1,
    ):
        stored = stored_by_id.get(point_id)
        if stored is None:
            raise RuntimeError(
                "rerank candidate missing from PostgreSQL: "
                f"point_id={point_id} chunk_id={result.chunk_id}"
            )
        if stored.chunk_id != result.chunk_id:
            raise RuntimeError(
                "rerank candidate identity mismatch: "
                f"point_id={point_id} "
                f"rrf_chunk_id={result.chunk_id} "
                f"postgres_chunk_id={stored.chunk_id}"
            )
        stored_chunks.append((original_rank, result, point_id, stored))

    inference_started = time.perf_counter()
    scores = reranker_provider.score(
        query=query,
        documents=[stored.text for _, _, _, stored in stored_chunks],
    )
    inference_ms = (time.perf_counter() - inference_started) * 1000.0

    if len(scores) != len(stored_chunks):
        raise RuntimeError(
            "reranker output count mismatch: "
            f"expected={len(stored_chunks)} got={len(scores)}"
        )

    scored = [
        (
            float(score),
            original_rank,
            result,
            point_id,
            stored,
        )
        for score, (original_rank, result, point_id, stored) in zip(
            scores,
            stored_chunks,
            strict=True,
        )
    ]

    sort_started = time.perf_counter()
    scored.sort(
        key=lambda item: (
            -item[0],
            item[1],
        )
    )

    reranked = []
    for rerank_rank, (
        score,
        original_rank,
        result,
        point_id,
        stored,
    ) in enumerate(scored, start=1):
        reranked.append(
            {
                "rerank_rank": rerank_rank,
                "original_rank": original_rank,
                "reranker_score": score,
                "point_id": point_id,
                "chunk_id": result.chunk_id,
                "document_id": stored.document_id,
                "document_name": stored.document_name,
                "chunk_index": stored.chunk_index,
                "section": stored.section,
                "rrf_score": result.score,
                "dense_rank": result.dense_rank,
                "bm25_rank": result.bm25_rank,
            }
        )

    sort_ms = (time.perf_counter() - sort_started) * 1000.0

    return (
        reranked,
        fetch_ms,
        inference_ms,
        sort_ms,
    )


async def warm_up(
    *,
    case: EvaluationCase,
    dense_service: DenseRetrievalService,
    chunk_repository: ChunkRepository,
    reranker_provider: CrossEncoderRerankerProvider,
) -> float:
    started = time.perf_counter()

    dense_hits = await dense_service.search(
        query=case.query,
        workspace_id=case.workspace_id,
        limit=1,
    )
    if not dense_hits:
        raise RuntimeError("warm-up dense retrieval returned no hits")

    stored_by_id = await chunk_repository.get_by_ids(
        chunk_ids=[dense_hits[0].point_id],
        workspace_id=case.workspace_id,
    )
    stored = stored_by_id.get(dense_hits[0].point_id)
    if stored is None:
        raise RuntimeError("warm-up chunk is missing from PostgreSQL")

    reranker_provider.score(
        query=case.query,
        documents=[stored.text],
    )

    return (time.perf_counter() - started) * 1000.0


async def evaluate(
    *,
    cases: list[EvaluationCase],
    candidate_limit: int,
    rerank_depth: int,
    top_k: int,
    rrf_k: int,
    reranker_provider: CrossEncoderRerankerProvider,
) -> list[dict]:
    dense_service = DenseRetrievalService(
        embedding_provider=get_embedding_provider(),
        vector_repository=get_vector_repository(),
    )

    outcomes: list[dict] = []

    async with AsyncSessionLocal() as session:
        bm25_service = BM25RetrievalService(
            chunk_repository=BM25ChunkRepository(session=session),
            k1=settings.bm25_k1,
            b=settings.bm25_b,
        )
        chunk_repository = ChunkRepository(session=session)

        warmup_ms = await warm_up(
            case=cases[0],
            dense_service=dense_service,
            chunk_repository=chunk_repository,
            reranker_provider=reranker_provider,
        )

        print()
        print("WARM-UP")
        print(f"model_and_dense_warmup_ms: {warmup_ms:.2f}")
        print("warm-up excluded from formal per-query latency")

        for case_index, case in enumerate(cases, start=1):
            pipeline_started = time.perf_counter()

            dense_started = time.perf_counter()
            dense_hits = await dense_service.search(
                query=case.query,
                workspace_id=case.workspace_id,
                limit=candidate_limit,
            )
            dense_ms = (time.perf_counter() - dense_started) * 1000.0

            bm25_started = time.perf_counter()
            bm25_hits = await bm25_service.search(
                query=case.query,
                workspace_id=case.workspace_id,
                limit=candidate_limit,
            )
            bm25_ms = (time.perf_counter() - bm25_started) * 1000.0

            dense_chunk_ids = [
                hit.payload.chunk_id
                for hit in dense_hits
            ]
            bm25_chunk_ids = [
                hit.chunk_id
                for hit in bm25_hits
            ]

            fusion_started = time.perf_counter()
            fused_results = reciprocal_rank_fusion(
                dense_chunk_ids=dense_chunk_ids,
                bm25_chunk_ids=bm25_chunk_ids,
                k=rrf_k,
            )
            fusion_ms = (time.perf_counter() - fusion_started) * 1000.0

            dense_by_chunk_id = {}
            for hit in dense_hits:
                dense_by_chunk_id.setdefault(
                    hit.payload.chunk_id,
                    hit,
                )

            bm25_by_chunk_id = {}
            for hit in bm25_hits:
                bm25_by_chunk_id.setdefault(
                    hit.chunk_id,
                    hit,
                )

            hybrid_candidate_ms = dense_ms + bm25_ms + fusion_ms

            reranked, fetch_ms, rerank_inference_ms, sort_ms = (
                await rerank_same_snapshot(
                    query=case.query,
                    workspace_id=case.workspace_id,
                    fused_results=fused_results,
                    dense_by_chunk_id=dense_by_chunk_id,
                    bm25_by_chunk_id=bm25_by_chunk_id,
                    chunk_repository=chunk_repository,
                    reranker_provider=reranker_provider,
                    rerank_depth=rerank_depth,
                    top_k=top_k,
                )
            )

            total_ms = (time.perf_counter() - pipeline_started) * 1000.0
            rerank_added_ms = fetch_ms + rerank_inference_ms + sort_ms

            fused_chunk_ids = [
                result.chunk_id
                for result in fused_results
            ]
            rerank_candidate_ids = fused_chunk_ids[:rerank_depth]
            reranked_chunk_ids = [
                item["chunk_id"]
                for item in reranked
            ]
            reranked_top_k = reranked[:top_k]

            dense_candidate_rank = rank_of(
                expected_chunk_id=case.expected_chunk_id,
                chunk_ids=dense_chunk_ids,
            )
            bm25_candidate_rank = rank_of(
                expected_chunk_id=case.expected_chunk_id,
                chunk_ids=bm25_chunk_ids,
            )
            hybrid_full_rank = rank_of(
                expected_chunk_id=case.expected_chunk_id,
                chunk_ids=fused_chunk_ids,
            )
            rerank_candidate_rank = rank_of(
                expected_chunk_id=case.expected_chunk_id,
                chunk_ids=rerank_candidate_ids,
            )

            dense_rank_at_k = rank_at_k(
                expected_chunk_id=case.expected_chunk_id,
                chunk_ids=dense_chunk_ids,
                top_k=top_k,
            )
            bm25_rank_at_k = rank_at_k(
                expected_chunk_id=case.expected_chunk_id,
                chunk_ids=bm25_chunk_ids,
                top_k=top_k,
            )
            hybrid_rank_at_k = (
                hybrid_full_rank
                if (
                    hybrid_full_rank is not None
                    and hybrid_full_rank <= top_k
                )
                else None
            )
            reranker_full_rank = rank_of(
                expected_chunk_id=case.expected_chunk_id,
                chunk_ids=reranked_chunk_ids,
            )
            reranker_rank_at_k = (
                reranker_full_rank
                if (
                    reranker_full_rank is not None
                    and reranker_full_rank <= top_k
                )
                else None
            )

            outcome = {
                "case_index": case_index,
                "query": case.query,
                "workspace_id": case.workspace_id,
                "expected_document_id": case.expected_document_id,
                "expected_document_name": case.expected_document_name,
                "expected_chunk_id": case.expected_chunk_id,
                "dense_rank_at_k": dense_rank_at_k,
                "bm25_rank_at_k": bm25_rank_at_k,
                "hybrid_rank_at_k": hybrid_rank_at_k,
                "reranker_rank_at_k": reranker_rank_at_k,
                "reranker_full_rank": reranker_full_rank,
                "dense_candidate_rank": dense_candidate_rank,
                "bm25_candidate_rank": bm25_candidate_rank,
                "hybrid_full_rank": hybrid_full_rank,
                "rerank_candidate_rank": rerank_candidate_rank,
                "dense_ms": dense_ms,
                "bm25_ms": bm25_ms,
                "fusion_ms": fusion_ms,
                "hybrid_candidate_ms": hybrid_candidate_ms,
                "postgres_fetch_ms": fetch_ms,
                "rerank_inference_ms": rerank_inference_ms,
                "rerank_sort_ms": sort_ms,
                "rerank_added_ms": rerank_added_ms,
                "reranked_total_ms": total_ms,
                "reranked_top_k": reranked_top_k,
            }
            outcomes.append(outcome)

            print(
                f"[{case_index:02d}/{len(cases)}] "
                f"dense={dense_candidate_rank} "
                f"bm25={bm25_candidate_rank} "
                f"hybrid={hybrid_full_rank} "
                f"rerank_candidate={rerank_candidate_rank} "
                f"rerank={reranker_full_rank} "
                f"rerank@{top_k}={reranker_rank_at_k} "
                f"rerank_ms={rerank_inference_ms:.2f}"
            )

    return outcomes


def summarize(
    *,
    outcomes: list[dict],
    top_k: int,
) -> dict:
    dense_metrics = metrics_from_ranks(
        ranks=[outcome["dense_rank_at_k"] for outcome in outcomes]
    )
    bm25_metrics = metrics_from_ranks(
        ranks=[outcome["bm25_rank_at_k"] for outcome in outcomes]
    )
    hybrid_metrics = metrics_from_ranks(
        ranks=[outcome["hybrid_rank_at_k"] for outcome in outcomes]
    )
    reranker_metrics = metrics_from_ranks(
        ranks=[outcome["reranker_rank_at_k"] for outcome in outcomes]
    )

    rescues = [
        outcome
        for outcome in outcomes
        if (
            outcome["hybrid_rank_at_k"] is None
            and outcome["reranker_rank_at_k"] is not None
        )
    ]
    regressions = [
        outcome
        for outcome in outcomes
        if (
            outcome["hybrid_rank_at_k"] is not None
            and outcome["reranker_rank_at_k"] is None
        )
    ]
    retained = [
        outcome
        for outcome in outcomes
        if (
            outcome["hybrid_rank_at_k"] is not None
            and outcome["reranker_rank_at_k"] is not None
        )
    ]
    retained_improved = [
        outcome
        for outcome in retained
        if outcome["reranker_rank_at_k"] < outcome["hybrid_rank_at_k"]
    ]
    retained_worsened = [
        outcome
        for outcome in retained
        if outcome["reranker_rank_at_k"] > outcome["hybrid_rank_at_k"]
    ]
    source_both_candidate_miss = [
        outcome
        for outcome in outcomes
        if (
            outcome["dense_candidate_rank"] is None
            and outcome["bm25_candidate_rank"] is None
        )
    ]
    rerank_candidate_miss = [
        outcome
        for outcome in outcomes
        if outcome["rerank_candidate_rank"] is None
    ]

    def compact(cases: list[dict]) -> list[dict]:
        return [
            {
                "case_index": outcome["case_index"],
                "query": outcome["query"],
                "hybrid_rank_at_k": outcome["hybrid_rank_at_k"],
                "hybrid_full_rank": outcome["hybrid_full_rank"],
                "rerank_candidate_rank": outcome["rerank_candidate_rank"],
                "reranker_rank_at_k": outcome["reranker_rank_at_k"],
                "reranker_full_rank": outcome["reranker_full_rank"],
                "dense_candidate_rank": outcome["dense_candidate_rank"],
                "bm25_candidate_rank": outcome["bm25_candidate_rank"],
            }
            for outcome in cases
        ]

    return {
        "metrics": {
            "dense": {
                "recall_at_k": dense_metrics.recall,
                "mrr_at_k": dense_metrics.mrr,
                "misses": dense_metrics.misses,
            },
            "bm25": {
                "recall_at_k": bm25_metrics.recall,
                "mrr_at_k": bm25_metrics.mrr,
                "misses": bm25_metrics.misses,
            },
            "hybrid": {
                "recall_at_k": hybrid_metrics.recall,
                "mrr_at_k": hybrid_metrics.mrr,
                "misses": hybrid_metrics.misses,
            },
            "hybrid_reranker": {
                "recall_at_k": reranker_metrics.recall,
                "mrr_at_k": reranker_metrics.mrr,
                "misses": reranker_metrics.misses,
            },
        },
        "failure_analysis": {
            "rescues": compact(rescues),
            "regressions": compact(regressions),
            "retained_hybrid_hits": len(retained),
            "retained_improved": compact(retained_improved),
            "retained_worsened": compact(retained_worsened),
            "source_both_candidate_miss": compact(source_both_candidate_miss),
            "rerank_candidate_miss": compact(rerank_candidate_miss),
        },
        "latency": {
            "hybrid_candidate": latency_summary(
                [outcome["hybrid_candidate_ms"] for outcome in outcomes]
            ),
            "postgres_fetch": latency_summary(
                [outcome["postgres_fetch_ms"] for outcome in outcomes]
            ),
            "rerank_inference": latency_summary(
                [outcome["rerank_inference_ms"] for outcome in outcomes]
            ),
            "rerank_added": latency_summary(
                [outcome["rerank_added_ms"] for outcome in outcomes]
            ),
            "reranked_total": latency_summary(
                [outcome["reranked_total_ms"] for outcome in outcomes]
            ),
        },
        "top_k": top_k,
    }


def print_metrics(
    *,
    name: str,
    metric: dict,
    top_k: int,
) -> None:
    print(
        f"{name:<18} "
        f"Recall@{top_k}={metric['recall_at_k']:.6f} "
        f"MRR@{top_k}={metric['mrr_at_k']:.6f} "
        f"MISS={metric['misses']}"
    )


def print_latency(
    *,
    name: str,
    metric: dict,
) -> None:
    print(
        f"{name:<18} "
        f"mean={metric['mean_ms']:.2f}ms "
        f"P50={metric['p50_ms']:.2f}ms "
        f"P95={metric['p95_ms']:.2f}ms "
        f"min={metric['min_ms']:.2f}ms "
        f"max={metric['max_ms']:.2f}ms"
    )


def write_jsonl(
    *,
    path: Path,
    outcomes: list[dict],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for outcome in outcomes:
            json.dump(outcome, file, ensure_ascii=False)
            file.write("\n")


def write_json(
    *,
    path: Path,
    payload: dict,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


async def run(args: argparse.Namespace) -> None:
    if args.candidate_limit <= 0:
        raise ValueError("candidate_limit must be greater than zero")
    if args.rerank_depth <= 0:
        raise ValueError("rerank_depth must be greater than zero")
    if args.top_k <= 0:
        raise ValueError("top_k must be greater than zero")
    if args.rrf_k <= 0:
        raise ValueError("rrf_k must be greater than zero")
    if args.rerank_depth > args.candidate_limit * 2:
        raise ValueError(
            "rerank_depth must not exceed twice candidate_limit"
        )
    if args.rerank_depth < args.top_k:
        raise ValueError(
            "rerank_depth must be greater than or equal to top_k"
        )

    cases = load_cases(args.dataset)
    workspace_ids = {case.workspace_id for case in cases}
    corpus_rows = await load_legal_corpus(workspace_ids=workspace_ids)

    dataset_hash = file_sha256(args.dataset)
    corpus_hash = corpus_snapshot_sha256(corpus_rows)
    document_identities = {
        (document.workspace_id, document.id)
        for _, document in corpus_rows
    }
    integrity_errors = validate_golden_integrity(
        cases=cases,
        corpus_rows=corpus_rows,
    )

    print("=" * 100)
    print("DAY 14 RERANKER RETRIEVAL EVALUATION")
    print("git_sha:", current_git_sha())
    print("git_dirty:", git_dirty())
    print("dataset:", args.dataset)
    print("dataset_sha256:", dataset_hash)
    print("evaluation_cases:", len(cases))
    print("workspace_ids:", sorted(workspace_ids))
    print("legal_documents:", len(document_identities))
    print("legal_chunks:", len(corpus_rows))
    print("corpus_snapshot_sha256:", corpus_hash)
    print("candidate_limit:", args.candidate_limit)
    print("rerank_depth:", args.rerank_depth)
    print("top_k:", args.top_k)
    print("rrf_k:", args.rrf_k)
    print("model:", args.model)
    print("batch_size:", args.batch_size)
    print("max_length:", args.max_length)

    if len(cases) != EXPECTED_CASES:
        raise RuntimeError(
            f"expected {EXPECTED_CASES} evaluation cases, got {len(cases)}"
        )
    if len(document_identities) != EXPECTED_LEGAL_DOCUMENTS:
        raise RuntimeError(
            "legal document count mismatch: "
            f"expected={EXPECTED_LEGAL_DOCUMENTS} "
            f"got={len(document_identities)}"
        )
    if len(corpus_rows) != EXPECTED_LEGAL_CHUNKS:
        raise RuntimeError(
            "legal chunk count mismatch: "
            f"expected={EXPECTED_LEGAL_CHUNKS} got={len(corpus_rows)}"
        )
    if dataset_hash != EXPECTED_DATASET_SHA256:
        raise RuntimeError(
            "dataset hash mismatch: "
            f"expected={EXPECTED_DATASET_SHA256} got={dataset_hash}"
        )
    if corpus_hash != EXPECTED_CORPUS_SHA256:
        raise RuntimeError(
            "corpus snapshot hash mismatch: "
            f"expected={EXPECTED_CORPUS_SHA256} got={corpus_hash}"
        )
    if integrity_errors:
        print()
        print("GOLDEN INTEGRITY: FAIL")
        for error in integrity_errors:
            print(" -", error)
        raise RuntimeError(
            "Golden dataset does not match the current legal corpus"
        )

    print()
    print(f"GOLDEN INTEGRITY: PASS ({len(cases)}/{len(cases)})")

    device = resolve_device(args.device)
    print("device:", device)

    reranker_provider = CrossEncoderRerankerProvider(
        model_name=args.model,
        batch_size=args.batch_size,
        max_length=args.max_length,
        device=device,
    )

    outcomes = await evaluate(
        cases=cases,
        candidate_limit=args.candidate_limit,
        rerank_depth=args.rerank_depth,
        top_k=args.top_k,
        rrf_k=args.rrf_k,
        reranker_provider=reranker_provider,
    )

    summary = summarize(
        outcomes=outcomes,
        top_k=args.top_k,
    )

    summary["config"] = {
        "candidate_limit": args.candidate_limit,
        "rerank_depth": args.rerank_depth,
        "top_k": args.top_k,
        "rrf_k": args.rrf_k,
        "model": args.model,
        "device": device,
        "batch_size": args.batch_size,
        "max_length": args.max_length,
    }
    summary["integrity"] = {
        "dataset_sha256": dataset_hash,
        "corpus_snapshot_sha256": corpus_hash,
        "evaluation_cases": len(cases),
        "legal_documents": len(document_identities),
        "legal_chunks": len(corpus_rows),
        "golden_valid": len(cases),
    }
    summary["git"] = {
        "sha": current_git_sha(),
        "dirty": git_dirty(),
    }

    print()
    print("=" * 100)
    print("METRICS")
    for name in ("dense", "bm25", "hybrid", "hybrid_reranker"):
        print_metrics(
            name=name,
            metric=summary["metrics"][name],
            top_k=args.top_k,
        )

    failure = summary["failure_analysis"]

    print()
    print("RERANKER FAILURE / BENEFIT SETS")
    print("rescues:", len(failure["rescues"]))
    print("regressions:", len(failure["regressions"]))
    print(
        "retained_hybrid_hits:",
        failure["retained_hybrid_hits"],
    )
    print(
        "retained_improved:",
        len(failure["retained_improved"]),
    )
    print(
        "retained_worsened:",
        len(failure["retained_worsened"]),
    )
    print(
        "source_both_candidate_miss:",
        len(failure["source_both_candidate_miss"]),
    )
    print(
        "rerank_candidate_miss:",
        len(failure["rerank_candidate_miss"]),
    )

    for title, key in (
        ("RESCUES", "rescues"),
        ("REGRESSIONS", "regressions"),
        ("RERANK CANDIDATE MISS", "rerank_candidate_miss"),
    ):
        print()
        print(title)
        cases_for_group = failure[key]
        if not cases_for_group:
            print("  none")
            continue
        for item in cases_for_group:
            print(
                f"  case={item['case_index']:02d} "
                f"hybrid={item['hybrid_full_rank']} "
                f"candidate={item['rerank_candidate_rank']} "
                f"rerank={item['reranker_full_rank']} "
                f"rerank@{args.top_k}={item['reranker_rank_at_k']} "
                f"query={item['query']}"
            )

    print()
    print("LATENCY")
    for name in (
        "hybrid_candidate",
        "postgres_fetch",
        "rerank_inference",
        "rerank_added",
        "reranked_total",
    ):
        print_latency(
            name=name,
            metric=summary["latency"][name],
        )

    write_jsonl(
        path=args.output,
        outcomes=outcomes,
    )
    write_json(
        path=args.summary,
        payload=summary,
    )

    print()
    print("result_report:", args.output)
    print("summary_report:", args.summary)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Dense, BM25, RRF Hybrid and Hybrid+CrossEncoder "
            "on the strict Day 14 Golden dataset."
        )
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("eval/retrieval_golden.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".local/day14/reranker_eval_results.jsonl"),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path(".local/day14/reranker_eval_summary.json"),
    )
    parser.add_argument("--candidate-limit", type=int, default=20)
    parser.add_argument("--rerank-depth", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument(
        "--model",
        default="BAAI/bge-reranker-v2-m3",
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="auto, mps, cuda, or cpu",
    )
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=512)
    return parser.parse_args()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
