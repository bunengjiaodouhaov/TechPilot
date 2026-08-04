from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.retrieval.bm25_dto import BM25SearchHit
from app.retrieval.bm25_repository import BM25ChunkRepository
from app.retrieval.bm25_retrieval_service import BM25RetrievalService


@dataclass(frozen=True)
class EvaluationCase:
    query: str
    workspace_id: int
    expected_document_id: int
    expected_document_name: str
    expected_chunk_id: str
    expected_chunk_index: int
    expected_section: str | None

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "EvaluationCase":
        return cls(
            query=str(data["query"]),
            workspace_id=int(data["workspace_id"]),
            expected_document_id=int(
                data["expected_document_id"]
            ),
            expected_document_name=str(
                data["expected_document_name"]
            ),
            expected_chunk_id=str(
                data["expected_chunk_id"]
            ),
            expected_chunk_index=int(
                data["expected_chunk_index"]
            ),
            expected_section=data.get(
                "expected_section"
            ),
        )


@dataclass(frozen=True)
class CaseResult:
    case: EvaluationCase
    hits: list[BM25SearchHit]
    relevant_rank: int | None

    @property
    def recalled(self) -> bool:
        return self.relevant_rank is not None

    @property
    def reciprocal_rank(self) -> float:
        if self.relevant_rank is None:
            return 0.0
        return 1.0 / self.relevant_rank


def load_cases(
    path: Path,
) -> list[EvaluationCase]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Evaluation dataset not found: {path}"
        )

    cases: list[EvaluationCase] = []

    for line_number, raw_line in enumerate(
        path.read_text(
            encoding="utf-8"
        ).splitlines(),
        start=1,
    ):
        line = raw_line.strip()

        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON on line "
                f"{line_number}: {exc}"
            ) from exc

        cases.append(
            EvaluationCase.from_dict(data)
        )

    if not cases:
        raise ValueError(
            "Evaluation dataset contains no cases"
        )

    return cases


def find_relevant_rank(
    *,
    expected_chunk_id: str,
    hits: list[BM25SearchHit],
) -> int | None:
    for rank, hit in enumerate(
        hits,
        start=1,
    ):
        if hit.chunk_id == expected_chunk_id:
            return rank

    return None


async def evaluate_case(
    *,
    service: BM25RetrievalService,
    case: EvaluationCase,
    top_k: int,
) -> CaseResult:
    hits = await service.search(
        query=case.query,
        workspace_id=case.workspace_id,
        limit=top_k,
    )

    return CaseResult(
        case=case,
        hits=hits,
        relevant_rank=find_relevant_rank(
            expected_chunk_id=(
                case.expected_chunk_id
            ),
            hits=hits,
        ),
    )


def print_case_result(
    *,
    index: int,
    result: CaseResult,
) -> None:
    status = (
        "HIT"
        if result.recalled
        else "MISS"
    )

    print()
    print("=" * 100)
    print(
        f"CASE {index}: {status}"
    )
    print(
        "query:",
        result.case.query,
    )
    print(
        "expected:",
        result.case.expected_document_name,
        (
            "chunk_index="
            f"{result.case.expected_chunk_index}"
        ),
        (
            "chunk_id="
            f"{result.case.expected_chunk_id}"
        ),
    )
    print(
        "relevant_rank:",
        result.relevant_rank,
    )
    print("-" * 100)

    for rank, hit in enumerate(
        result.hits,
        start=1,
    ):
        marker = (
            " <-- EXPECTED"
            if hit.chunk_id
            == result.case.expected_chunk_id
            else ""
        )

        print(
            f"{rank}. "
            f"score={hit.score:.6f} "
            f"document={hit.document_name} "
            f"chunk_index={hit.chunk_index} "
            f"chunk_id={hit.chunk_id}"
            f"{marker}"
        )
        print(
            f"   section={hit.section}"
        )


async def run_evaluation(
    *,
    dataset_path: Path,
    top_k: int,
) -> None:
    if top_k <= 0:
        raise ValueError(
            "top_k must be greater than zero"
        )

    cases = load_cases(dataset_path)

    async with AsyncSessionLocal() as session:
        service = BM25RetrievalService(
            chunk_repository=BM25ChunkRepository(
                session=session
            ),
            k1=settings.bm25_k1,
            b=settings.bm25_b,
        )

        results: list[CaseResult] = []

        for index, case in enumerate(
            cases,
            start=1,
        ):
            result = await evaluate_case(
                service=service,
                case=case,
                top_k=top_k,
            )

            results.append(result)

            print_case_result(
                index=index,
                result=result,
            )

    recalled_count = sum(
        result.recalled
        for result in results
    )

    recall_at_k = (
        recalled_count
        / len(results)
    )

    mrr_at_k = (
        sum(
            result.reciprocal_rank
            for result in results
        )
        / len(results)
    )

    failures = [
        result
        for result in results
        if not result.recalled
    ]

    print()
    print("=" * 100)
    print("BM25 RETRIEVAL BASELINE")
    print(
        "dataset:",
        dataset_path,
    )
    print(
        "evaluation_cases:",
        len(results),
    )
    print(
        "top_k:",
        top_k,
    )
    print(
        "recalled_cases:",
        recalled_count,
    )
    print(
        f"recall_at_{top_k}: "
        f"{recall_at_k:.6f}"
    )
    print(
        f"mrr_at_{top_k}: "
        f"{mrr_at_k:.6f}"
    )

    output_path = (
        dataset_path.parent
        / "bm25_retrieval_failures.jsonl"
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        for result in failures:
            json.dump(
                {
                    "query": (
                        result.case.query
                    ),
                    "workspace_id": (
                        result.case.workspace_id
                    ),
                    "expected_document_id": (
                        result.case
                        .expected_document_id
                    ),
                    "expected_document_name": (
                        result.case
                        .expected_document_name
                    ),
                    "expected_chunk_id": (
                        result.case
                        .expected_chunk_id
                    ),
                    "expected_chunk_index": (
                        result.case
                        .expected_chunk_index
                    ),
                    "expected_section": (
                        result.case
                        .expected_section
                    ),
                    "retrieved": [
                        {
                            "rank": rank,
                            "score": hit.score,
                            "document_id": (
                                hit.document_id
                            ),
                            "document_name": (
                                hit.document_name
                            ),
                            "chunk_id": (
                                hit.chunk_id
                            ),
                            "chunk_index": (
                                hit.chunk_index
                            ),
                            "section": (
                                hit.section
                            ),
                        }
                        for rank, hit in enumerate(
                            result.hits,
                            start=1,
                        )
                    ],
                },
                file,
                ensure_ascii=False,
            )
            file.write("\n")

    print(
        "failure_report:",
        output_path,
        f"({len(failures)} cases)",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate TechPilot BM25 retrieval."
        ),
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(
            "eval/retrieval_golden.jsonl"
        ),
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    asyncio.run(
        run_evaluation(
            dataset_path=args.dataset,
            top_k=args.top_k,
        )
    )


if __name__ == "__main__":
    main()
