from typing import Any, cast

import pytest

from app.answering.answer_service import (
    AnswerService,
    WorkspaceNotFoundError,
)


class MissingWorkspaceRepository:
    async def exists(self, *, workspace_id: int) -> bool:
        return False


class RetrievalServiceThatMustNotRun:
    async def search(self, **kwargs: object) -> object:
        raise AssertionError(
            "retrieval must not run when workspace does not exist"
        )


@pytest.mark.asyncio
async def test_answer_raises_when_workspace_does_not_exist() -> None:
    service = AnswerService(
        retrieval_service=cast(
            Any,
            RetrievalServiceThatMustNotRun(),
        ),
        chunk_repository=cast(Any, object()),
        context_enricher=cast(Any, object()),
        context_builder=cast(Any, object()),
        llm_provider=cast(Any, object()),
        workspace_repository=cast(
            Any,
            MissingWorkspaceRepository(),
        ),
    )

    with pytest.raises(
        WorkspaceNotFoundError,
        match="workspace 999 was not found",
    ):
        await service.answer(
            question="什么是 RAG？",
            workspace_id=999,
        )
