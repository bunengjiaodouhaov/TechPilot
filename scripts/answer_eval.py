from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.answering.answer_service import AnswerService
from app.answering.chunk_repository import ChunkRepository
from app.answering.context_builder import ContextBuilder
from app.answering.context_enricher import ContextEnricher
from app.answering.workspace_repository import WorkspaceRepository
from app.api.dependencies import (
    get_dense_retrieval_service,
    get_llm_provider,
)
from app.core.config import settings
from app.db.session import AsyncSessionLocal


@dataclass(frozen=True)
class AnswerEvaluationCase:
    """One manually labelled trusted-answering evaluation case."""

    id: str
    question: str
    workspace_id: int
    answerable: bool
    reference_answer: str | None
    expected_document_names: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    category: str
    notes: str

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "AnswerEvaluationCase":
        case = cls(
            id=str(data["id"]).strip(),
            question=str(data["question"]).strip(),
            workspace_id=int(data["workspace_id"]),
            answerable=bool(data["answerable"]),
            reference_answer=(
                str(data["reference_answer"]).strip()
                if data.get("reference_answer") is not None
                else None
            ),
            expected_document_names=tuple(
                str(name)
                for name in data.get(
                    "expected_document_names",
                    [],
                )
            ),
            acceptance_criteria=tuple(
                str(criterion)
                for criterion in data.get(
                    "acceptance_criteria",
                    [],
                )
            ),
            category=str(data.get("category", "")).strip(),
            notes=str(data.get("notes", "")).strip(),
        )
        case.validate()
        return case

    def validate(self) -> None:
        if not self.id:
            raise ValueError("case id must not be empty")

        if not self.question:
            raise ValueError(
                f"question must not be empty: {self.id}"
            )

        if self.workspace_id <= 0:
            raise ValueError(
                f"workspace_id must be positive: {self.id}"
            )

        if self.answerable and not self.reference_answer:
            raise ValueError(
                "answerable case must provide reference_answer: "
                f"{self.id}"
            )

        if not self.answerable and self.reference_answer is not None:
            raise ValueError(
                "unanswerable case must use null reference_answer: "
                f"{self.id}"
            )


@dataclass(frozen=True)
class AnswerEvaluationResult:
    """Raw system output for one answer evaluation case."""

    case: AnswerEvaluationCase
    answer_text: str | None
    refused: bool | None
    citations: tuple[dict[str, Any], ...]
    error: str | None


def load_cases(
    path: Path,
) -> list[AnswerEvaluationCase]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Answer evaluation dataset not found: {path}"
        )

    cases: list[AnswerEvaluationCase] = []
    seen_ids: set[str] = set()

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()

        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON on line {line_number}: {exc}"
            ) from exc

        case = AnswerEvaluationCase.from_dict(data)

        if case.id in seen_ids:
            raise ValueError(
                f"Duplicate case id on line {line_number}: "
                f"{case.id}"
            )

        seen_ids.add(case.id)
        cases.append(case)

    if not cases:
        raise ValueError(
            "Answer evaluation dataset contains no cases"
        )

    return cases


def build_answer_service(
    *,
    session: Any,
) -> AnswerService:
    return AnswerService(
        retrieval_service=get_dense_retrieval_service(),
        chunk_repository=ChunkRepository(session=session),
        context_enricher=ContextEnricher(),
        context_builder=ContextBuilder(
            max_characters=(
                settings.answer_context_max_characters
            ),
        ),
        llm_provider=get_llm_provider(),
        workspace_repository=WorkspaceRepository(
            session=session,
        ),
    )


async def evaluate_case(
    *,
    service: AnswerService,
    case: AnswerEvaluationCase,
    retrieval_limit: int,
) -> AnswerEvaluationResult:
    try:
        answer = await service.answer(
            question=case.question,
            workspace_id=case.workspace_id,
            retrieval_limit=retrieval_limit,
        )
    except Exception as exc:
        return AnswerEvaluationResult(
            case=case,
            answer_text=None,
            refused=None,
            citations=(),
            error=f"{type(exc).__name__}: {exc}",
        )

    citations = tuple(
        {
            "chunk_id": citation.chunk_id,
            "document_id": citation.document_id,
            "document_name": citation.document_name,
            "page_start": citation.page_start,
            "page_end": citation.page_end,
            "section": citation.section,
            "quote": citation.quote,
        }
        for citation in answer.citations
    )

    return AnswerEvaluationResult(
        case=case,
        answer_text=answer.text,
        refused=answer.refused,
        citations=citations,
        error=None,
    )


def write_results(
    *,
    path: Path,
    results: list[AnswerEvaluationResult],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:
        for result in results:
            payload = {
                "case": asdict(result.case),
                "actual": {
                    "answer_text": result.answer_text,
                    "refused": result.refused,
                    "citations": list(result.citations),
                    "error": result.error,
                },
                "manual_evaluation": {
                    "answer_correct": None,
                    "citation_supported": None,
                    "failure_type": None,
                    "notes": "",
                },
            }

            json.dump(
                payload,
                file,
                ensure_ascii=False,
            )
            file.write("\n")


async def run_evaluation(
    *,
    dataset_path: Path,
    output_path: Path,
    retrieval_limit: int,
) -> None:
    if retrieval_limit <= 0:
        raise ValueError(
            "retrieval_limit must be greater than zero"
        )

    cases = load_cases(dataset_path)

    results: list[AnswerEvaluationResult] = []

    async with AsyncSessionLocal() as session:
        service = build_answer_service(
            session=session,
        )

        for index, case in enumerate(
            cases,
            start=1,
        ):
            print(
                f"[{index}/{len(cases)}] "
                f"{case.id}: {case.question}"
            )

            result = await evaluate_case(
                service=service,
                case=case,
                retrieval_limit=retrieval_limit,
            )
            results.append(result)

            if result.error is not None:
                print(f"  ERROR: {result.error}")
            else:
                print(
                    "  refused:",
                    result.refused,
                    "citations:",
                    len(result.citations),
                )

    write_results(
        path=output_path,
        results=results,
    )

    error_count = sum(
        1
        for result in results
        if result.error is not None
    )

    print()
    print("=" * 80)
    print("TRUSTED ANSWERING EVALUATION")
    print("dataset:", dataset_path)
    print("cases:", len(results))
    print("runtime_errors:", error_count)
    print("results:", output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run TechPilot trusted-answering evaluation."
        ),
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("eval/answer_golden.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval/answer_results.jsonl"),
    )
    parser.add_argument(
        "--retrieval-limit",
        type=int,
        default=5,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    asyncio.run(
        run_evaluation(
            dataset_path=args.dataset,
            output_path=args.output,
            retrieval_limit=args.retrieval_limit,
        )
    )


if __name__ == "__main__":
    main()