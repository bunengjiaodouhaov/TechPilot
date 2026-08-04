from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.api.dependencies import (
    get_embedding_provider,
    get_vector_repository,
)
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.document_status import DocumentStatus
from app.retrieval.bm25_repository import BM25ChunkRepository
from app.retrieval.bm25_retrieval_service import BM25RetrievalService
from app.retrieval.dense_retrieval_service import DenseRetrievalService
from app.retrieval.rrf import reciprocal_rank_fusion
from scripts.retrieval_eval import EvaluationCase, load_cases


@dataclass(frozen=True)
class RetrievalOutcome:
    case_index: int
    case: EvaluationCase

    dense_rank: int | None
    bm25_rank: int | None
    hybrid_rank: int | None

    dense_candidate_rank: int | None
    bm25_candidate_rank: int | None

    dense_hits: list
    bm25_hits: list
    fused_results: list


@dataclass(frozen=True)
class Metrics:
    recalled: int
    recall: float
    mrr: float
    misses: int


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for block in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def current_git_sha() -> str:
    try:
        result = subprocess.run(
            [
                "git",
                "rev-parse",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"

    return result.stdout.strip()


def rank_of(
    *,
    expected_chunk_id: str,
    chunk_ids: list[str],
) -> int | None:
    for rank, chunk_id in enumerate(
        chunk_ids,
        start=1,
    ):
        if chunk_id == expected_chunk_id:
            return rank

    return None


def metrics_from_ranks(
    *,
    ranks: list[int | None],
) -> Metrics:
    recalled = sum(
        rank is not None
        for rank in ranks
    )

    mrr = (
        sum(
            0.0
            if rank is None
            else 1.0 / rank
            for rank in ranks
        )
        / len(ranks)
    )

    return Metrics(
        recalled=recalled,
        recall=recalled / len(ranks),
        mrr=mrr,
        misses=len(ranks) - recalled,
    )


async def load_legal_corpus(
    *,
    workspace_ids: set[int],
) -> list[tuple[Chunk, Document]]:
    async with AsyncSessionLocal() as session:
        statement = (
            select(
                Chunk,
                Document,
            )
            .join(
                Document,
                Chunk.document_id == Document.id,
            )
            .where(
                Document.workspace_id.in_(
                    workspace_ids
                ),
                Document.deleted_at.is_(None),
                Document.status.in_(
                    (
                        DocumentStatus.COMPLETED.value,
                        DocumentStatus.PARTIAL.value,
                    )
                ),
            )
            .order_by(
                Document.workspace_id,
                Document.id,
                Chunk.id,
            )
        )

        result = await session.execute(statement)

        return list(result.all())


def corpus_snapshot_sha256(
    rows: list[tuple[Chunk, Document]],
) -> str:
    digest = hashlib.sha256()

    identities = sorted(
        (
            document.workspace_id,
            document.id,
            chunk.chunk_id,
            chunk.chunk_index,
        )
        for chunk, document in rows
    )

    for identity in identities:
        digest.update(
            (
                "|".join(
                    str(value)
                    for value in identity
                )
                + "\n"
            ).encode("utf-8")
        )

    return digest.hexdigest()


def validate_golden_integrity(
    *,
    cases: list[EvaluationCase],
    corpus_rows: list[tuple[Chunk, Document]],
) -> list[str]:
    by_identity = {
        (
            document.workspace_id,
            document.id,
            chunk.chunk_id,
        ): (
            chunk,
            document,
        )
        for chunk, document in corpus_rows
    }

    errors: list[str] = []

    for case_index, case in enumerate(
        cases,
        start=1,
    ):
        identity = (
            case.workspace_id,
            case.expected_document_id,
            case.expected_chunk_id,
        )

        row = by_identity.get(identity)

        if row is None:
            errors.append(
                (
                    f"case={case_index} "
                    "expected chunk is not in legal corpus: "
                    f"workspace_id={case.workspace_id} "
                    f"document_id={case.expected_document_id} "
                    f"chunk_id={case.expected_chunk_id}"
                )
            )
            continue

        chunk, document = row

        if document.name != case.expected_document_name:
            errors.append(
                (
                    f"case={case_index} "
                    "document name mismatch: "
                    f"golden={case.expected_document_name!r} "
                    f"corpus={document.name!r}"
                )
            )

        if chunk.chunk_index != case.expected_chunk_index:
            errors.append(
                (
                    f"case={case_index} "
                    "chunk index mismatch: "
                    f"golden={case.expected_chunk_index} "
                    f"corpus={chunk.chunk_index}"
                )
            )

        if (
            case.expected_section is not None
            and chunk.section != case.expected_section
        ):
            errors.append(
                (
                    f"case={case_index} "
                    "section mismatch: "
                    f"golden={case.expected_section!r} "
                    f"corpus={chunk.section!r}"
                )
            )

    return errors


async def evaluate(
    *,
    cases: list[EvaluationCase],
    candidate_limit: int,
    top_k: int,
    rrf_k: int,
) -> list[RetrievalOutcome]:
    dense_service = DenseRetrievalService(
        embedding_provider=get_embedding_provider(),
        vector_repository=get_vector_repository(),
    )

    outcomes: list[RetrievalOutcome] = []

    async with AsyncSessionLocal() as session:
        bm25_service = BM25RetrievalService(
            chunk_repository=BM25ChunkRepository(
                session=session
            ),
            k1=settings.bm25_k1,
            b=settings.bm25_b,
        )

        for case_index, case in enumerate(
            cases,
            start=1,
        ):
            dense_hits = await dense_service.search(
                query=case.query,
                workspace_id=case.workspace_id,
                limit=candidate_limit,
            )

            bm25_hits = await bm25_service.search(
                query=case.query,
                workspace_id=case.workspace_id,
                limit=candidate_limit,
            )

            dense_chunk_ids = [
                hit.payload.chunk_id
                for hit in dense_hits
            ]
            bm25_chunk_ids = [
                hit.chunk_id
                for hit in bm25_hits
            ]

            fused_results = reciprocal_rank_fusion(
                dense_chunk_ids=dense_chunk_ids,
                bm25_chunk_ids=bm25_chunk_ids,
                k=rrf_k,
            )

            fused_chunk_ids = [
                result.chunk_id
                for result in fused_results
            ]

            dense_candidate_rank = rank_of(
                expected_chunk_id=case.expected_chunk_id,
                chunk_ids=dense_chunk_ids,
            )
            bm25_candidate_rank = rank_of(
                expected_chunk_id=case.expected_chunk_id,
                chunk_ids=bm25_chunk_ids,
            )

            dense_rank = (
                dense_candidate_rank
                if (
                    dense_candidate_rank is not None
                    and dense_candidate_rank <= top_k
                )
                else None
            )

            bm25_rank = (
                bm25_candidate_rank
                if (
                    bm25_candidate_rank is not None
                    and bm25_candidate_rank <= top_k
                )
                else None
            )

            hybrid_full_rank = rank_of(
                expected_chunk_id=case.expected_chunk_id,
                chunk_ids=fused_chunk_ids,
            )

            hybrid_rank = (
                hybrid_full_rank
                if (
                    hybrid_full_rank is not None
                    and hybrid_full_rank <= top_k
                )
                else None
            )

            outcomes.append(
                RetrievalOutcome(
                    case_index=case_index,
                    case=case,
                    dense_rank=dense_rank,
                    bm25_rank=bm25_rank,
                    hybrid_rank=hybrid_rank,
                    dense_candidate_rank=(
                        dense_candidate_rank
                    ),
                    bm25_candidate_rank=(
                        bm25_candidate_rank
                    ),
                    dense_hits=dense_hits,
                    bm25_hits=bm25_hits,
                    fused_results=fused_results,
                )
            )

            print(
                f"[{case_index:02d}/{len(cases)}] "
                f"dense={dense_candidate_rank} "
                f"bm25={bm25_candidate_rank} "
                f"hybrid={hybrid_full_rank}"
            )

    return outcomes


def serialize_outcome(
    *,
    outcome: RetrievalOutcome,
    top_k: int,
) -> dict:
    return {
        "case_index": outcome.case_index,
        "query": outcome.case.query,
        "workspace_id": outcome.case.workspace_id,
        "expected_document_id": (
            outcome.case.expected_document_id
        ),
        "expected_document_name": (
            outcome.case.expected_document_name
        ),
        "expected_chunk_id": (
            outcome.case.expected_chunk_id
        ),
        "dense_rank_at_k": outcome.dense_rank,
        "bm25_rank_at_k": outcome.bm25_rank,
        "hybrid_rank_at_k": outcome.hybrid_rank,
        "dense_candidate_rank": (
            outcome.dense_candidate_rank
        ),
        "bm25_candidate_rank": (
            outcome.bm25_candidate_rank
        ),
        "dense_top_k": [
            {
                "rank": rank,
                "score": hit.score,
                "document_id": (
                    hit.payload.document_id
                ),
                "document_name": (
                    hit.payload.document_name
                ),
                "chunk_id": (
                    hit.payload.chunk_id
                ),
                "chunk_index": (
                    hit.payload.chunk_index
                ),
            }
            for rank, hit in enumerate(
                outcome.dense_hits[:top_k],
                start=1,
            )
        ],
        "bm25_top_k": [
            {
                "rank": rank,
                "score": hit.score,
                "document_id": hit.document_id,
                "document_name": hit.document_name,
                "chunk_id": hit.chunk_id,
                "chunk_index": hit.chunk_index,
            }
            for rank, hit in enumerate(
                outcome.bm25_hits[:top_k],
                start=1,
            )
        ],
        "hybrid_top_k": [
            {
                "rank": rank,
                "chunk_id": result.chunk_id,
                "rrf_score": result.score,
                "dense_rank": result.dense_rank,
                "bm25_rank": result.bm25_rank,
            }
            for rank, result in enumerate(
                outcome.fused_results[:top_k],
                start=1,
            )
        ],
    }


def print_metrics(
    *,
    name: str,
    metrics: Metrics,
    top_k: int,
) -> None:
    print(
        f"{name:<8} "
        f"Recall@{top_k}={metrics.recall:.6f} "
        f"MRR@{top_k}={metrics.mrr:.6f} "
        f"MISS={metrics.misses}"
    )


def print_case_group(
    *,
    title: str,
    outcomes: list[RetrievalOutcome],
) -> None:
    print()
    print(title, f"({len(outcomes)})")

    if not outcomes:
        print("  none")
        return

    for outcome in outcomes:
        print(
            f"  case={outcome.case_index:02d} "
            f"dense={outcome.dense_candidate_rank} "
            f"bm25={outcome.bm25_candidate_rank} "
            f"hybrid={outcome.hybrid_rank} "
            f"query={outcome.case.query}"
        )


def write_results(
    *,
    output_path: Path,
    outcomes: list[RetrievalOutcome],
    top_k: int,
) -> None:
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        for outcome in outcomes:
            json.dump(
                serialize_outcome(
                    outcome=outcome,
                    top_k=top_k,
                ),
                file,
                ensure_ascii=False,
            )
            file.write("\n")


async def run(
    *,
    dataset_path: Path,
    output_path: Path,
    candidate_limit: int,
    top_k: int,
    rrf_k: int,
) -> None:
    if candidate_limit <= 0:
        raise ValueError(
            "candidate_limit must be greater than zero"
        )

    if top_k <= 0:
        raise ValueError(
            "top_k must be greater than zero"
        )

    if candidate_limit < top_k:
        raise ValueError(
            "candidate_limit must be greater than "
            "or equal to top_k"
        )

    if rrf_k <= 0:
        raise ValueError(
            "rrf_k must be greater than zero"
        )

    cases = load_cases(dataset_path)

    workspace_ids = {
        case.workspace_id
        for case in cases
    }

    corpus_rows = await load_legal_corpus(
        workspace_ids=workspace_ids,
    )

    document_identities = {
        (
            document.workspace_id,
            document.id,
        )
        for _, document in corpus_rows
    }

    dataset_hash = file_sha256(
        dataset_path
    )
    corpus_hash = corpus_snapshot_sha256(
        corpus_rows
    )
    git_sha = current_git_sha()

    print("=" * 100)
    print("DAY 13 HYBRID RETRIEVAL EVALUATION")
    print("git_sha:", git_sha)
    print("dataset:", dataset_path)
    print("dataset_sha256:", dataset_hash)
    print("evaluation_cases:", len(cases))
    print("workspace_ids:", sorted(workspace_ids))
    print(
        "legal_documents:",
        len(document_identities),
    )
    print(
        "legal_chunks:",
        len(corpus_rows),
    )
    print(
        "corpus_snapshot_sha256:",
        corpus_hash,
    )
    print(
        "candidate_limit:",
        candidate_limit,
    )
    print(
        "top_k:",
        top_k,
    )
    print(
        "rrf_k:",
        rrf_k,
    )
    print(
        "bm25_k1:",
        settings.bm25_k1,
    )
    print(
        "bm25_b:",
        settings.bm25_b,
    )

    integrity_errors = validate_golden_integrity(
        cases=cases,
        corpus_rows=corpus_rows,
    )

    print()
    print("GOLDEN INTEGRITY")
    print(
        "valid:",
        len(cases) - len(integrity_errors),
        "/",
        len(cases),
    )

    if integrity_errors:
        print(
            "RESULT: FAIL — evaluation aborted"
        )

        for error in integrity_errors:
            print(" -", error)

        raise RuntimeError(
            "Golden dataset does not match "
            "the current legal corpus"
        )

    print("RESULT: PASS")

    outcomes = await evaluate(
        cases=cases,
        candidate_limit=candidate_limit,
        top_k=top_k,
        rrf_k=rrf_k,
    )

    dense_metrics = metrics_from_ranks(
        ranks=[
            outcome.dense_rank
            for outcome in outcomes
        ]
    )
    bm25_metrics = metrics_from_ranks(
        ranks=[
            outcome.bm25_rank
            for outcome in outcomes
        ]
    )
    hybrid_metrics = metrics_from_ranks(
        ranks=[
            outcome.hybrid_rank
            for outcome in outcomes
        ]
    )

    dense_only = [
        outcome
        for outcome in outcomes
        if (
            outcome.dense_rank is not None
            and outcome.bm25_rank is None
        )
    ]

    bm25_only = [
        outcome
        for outcome in outcomes
        if (
            outcome.dense_rank is None
            and outcome.bm25_rank is not None
        )
    ]

    both_hit = [
        outcome
        for outcome in outcomes
        if (
            outcome.dense_rank is not None
            and outcome.bm25_rank is not None
        )
    ]

    both_miss = [
        outcome
        for outcome in outcomes
        if (
            outcome.dense_rank is None
            and outcome.bm25_rank is None
        )
    ]

    fusion_losses = [
        outcome
        for outcome in outcomes
        if (
            (
                outcome.dense_rank is not None
                or outcome.bm25_rank is not None
            )
            and outcome.hybrid_rank is None
        )
    ]

    hybrid_rescues = [
        outcome
        for outcome in outcomes
        if (
            outcome.dense_rank is None
            and outcome.bm25_rank is None
            and outcome.hybrid_rank is not None
        )
    ]

    candidate_both_miss = [
        outcome
        for outcome in outcomes
        if (
            outcome.dense_candidate_rank is None
            and outcome.bm25_candidate_rank is None
        )
    ]

    preserved_dense_only = sum(
        outcome.hybrid_rank is not None
        for outcome in dense_only
    )

    preserved_bm25_only = sum(
        outcome.hybrid_rank is not None
        for outcome in bm25_only
    )

    print()
    print("=" * 100)
    print("METRICS")
    print_metrics(
        name="Dense",
        metrics=dense_metrics,
        top_k=top_k,
    )
    print_metrics(
        name="BM25",
        metrics=bm25_metrics,
        top_k=top_k,
    )
    print_metrics(
        name="Hybrid",
        metrics=hybrid_metrics,
        top_k=top_k,
    )

    print()
    print("TOP-K FAILURE SETS")
    print("dense_only_hits:", len(dense_only))
    print("bm25_only_hits:", len(bm25_only))
    print("both_hits:", len(both_hit))
    print("both_miss:", len(both_miss))
    print(
        "preserved_dense_only:",
        f"{preserved_dense_only}/{len(dense_only)}",
    )
    print(
        "preserved_bm25_only:",
        f"{preserved_bm25_only}/{len(bm25_only)}",
    )
    print(
        "fusion_losses:",
        len(fusion_losses),
    )
    print(
        "hybrid_rescues_from_both_top_k_miss:",
        len(hybrid_rescues),
    )
    print(
        "both_candidate_miss:",
        len(candidate_both_miss),
    )

    print_case_group(
        title="FUSION LOSSES",
        outcomes=fusion_losses,
    )
    print_case_group(
        title="HYBRID RESCUES",
        outcomes=hybrid_rescues,
    )
    print_case_group(
        title="BOTH CANDIDATE MISS",
        outcomes=candidate_both_miss,
    )

    write_results(
        output_path=output_path,
        outcomes=outcomes,
        top_k=top_k,
    )

    print()
    print(
        "result_report:",
        output_path,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate Dense, BM25 and RRF Hybrid "
            "on one shared candidate snapshot."
        )
    )

    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(
            "eval/retrieval_golden.jsonl"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "eval/hybrid_retrieval_results.jsonl"
        ),
    )

    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--rrf-k",
        type=int,
        default=60,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    asyncio.run(
        run(
            dataset_path=args.dataset,
            output_path=args.output,
            candidate_limit=args.candidate_limit,
            top_k=args.top_k,
            rrf_k=args.rrf_k,
        )
    )


if __name__ == "__main__":
    main()
