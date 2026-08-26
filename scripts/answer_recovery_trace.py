from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.answering.answer_service import AnswerService
from app.answering.chunk_repository import ChunkRepository
from app.answering.context_enricher import ContextEnricher
from app.api.dependencies import get_answer_retrieval_service
from app.core.config import settings
from app.db.session import AsyncSessionLocal


async def run(*, workspace_id: int, question: str) -> None:
    async with AsyncSessionLocal() as session:
        retrieval = get_answer_retrieval_service(session=session)
        repository = ChunkRepository(session=session)

        first_hits = await retrieval.search(
            query=question,
            workspace_id=workspace_id,
            limit=5,
        )
        first_stored = await repository.get_by_ids(
            chunk_ids=[hit.point_id for hit in first_hits],
            workspace_id=workspace_id,
        )
        first_contexts = ContextEnricher().enrich(
            hits=first_hits,
            stored_chunks=first_stored,
        ).contexts

        anchors = await retrieval.search(
            query=question,
            workspace_id=workspace_id,
            limit=settings.answer_recovery_anchor_limit,
        )
        groups = AnswerService._group_parent_sections(anchors)
        selected = groups[: settings.answer_recovery_parent_group_limit]
        siblings = await repository.get_by_parent_sections(
            parent_sections=[key for key, _ in selected],
            workspace_id=workspace_id,
        )
        additions = AnswerService._rank_recovery_chunks(
            chunks=siblings,
            selected_groups=selected,
            exclude_chunk_ids={context.chunk_db_id for context in first_contexts},
        )[: settings.answer_recovery_max_additions]

        print("=== CONFIG ===")
        print("reranker_model:", settings.reranker_model)
        print("recovery_enabled:", settings.answer_recovery_enabled)
        print("anchor_limit:", settings.answer_recovery_anchor_limit)
        print("parent_group_limit:", settings.answer_recovery_parent_group_limit)
        print("max_additions:", settings.answer_recovery_max_additions)

        print("\n=== FIRST PASS TOP5 ===")
        for rank, hit in enumerate(first_hits, start=1):
            stored = first_stored.get(hit.point_id)
            print(
                f"rank={rank:02d} point={hit.point_id} score={hit.score:.6f} "
                f"chunk_index={stored.chunk_index if stored else 'MISSING'} "
                f"section={stored.section if stored else hit.payload.section}"
            )

        print("\n=== RECOVERY TOP20 ANCHORS ===")
        for rank, hit in enumerate(anchors, start=1):
            parent = AnswerService._parent_section(hit.payload.section)
            print(
                f"rank={rank:02d} point={hit.point_id} score={hit.score:.6f} "
                f"chunk_index={hit.payload.chunk_index} parent={parent!r} "
                f"section={hit.payload.section}"
            )

        print("\n=== PARENT GROUPS ===")
        for order, (key, values) in enumerate(groups, start=1):
            document_id, parent = key
            anchor_ranks = [rank for rank, _ in values]
            anchor_indices = [index for _, index in values]
            selected_mark = " SELECTED" if order <= settings.answer_recovery_parent_group_limit else ""
            print(
                f"group={order:02d}{selected_mark} document_id={document_id} "
                f"support={len(values)} best_rank={min(anchor_ranks)} "
                f"anchor_indices={anchor_indices} parent={parent}"
            )

        print("\n=== RECOVERY ADDITIONS ===")
        if not additions:
            print("NONE")
        for order, chunk in enumerate(additions, start=1):
            print(
                f"add={order:02d} point={chunk.chunk_db_id} "
                f"chunk_index={chunk.chunk_index} section={chunk.section}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trace bounded structural answer recovery without calling verifier/LLM.")
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--question", required=True)
    args = parser.parse_args()
    if args.workspace_id <= 0:
        parser.error("--workspace-id must be positive")
    if not args.question.strip():
        parser.error("--question must not be empty")
    return args


def main() -> None:
    args = parse_args()
    asyncio.run(run(workspace_id=args.workspace_id, question=args.question.strip()))


if __name__ == "__main__":
    main()
