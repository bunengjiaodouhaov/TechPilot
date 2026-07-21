from typing import Any

import pytest

from app.answering.answer_service import (
    REFUSAL_TEXT,
    AnswerService,
    AnsweringDataConsistencyError,
    InvalidLLMCitationError,
)
from app.answering.context_builder import ContextBuilder
from app.answering.context_enricher import ContextEnricher
from app.answering.dto import LLMAnswer, StoredChunk
from app.retrieval.dto import (
    ChunkVectorPayload,
    VectorSearchHit,
)


class FakeRetrievalService:
    def __init__(
        self,
        *,
        hits: list[VectorSearchHit],
    ) -> None:
        self.hits = hits
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        limit: int,
    ) -> list[VectorSearchHit]:
        self.calls.append(
            {
                "query": query,
                "workspace_id": workspace_id,
                "limit": limit,
            }
        )
        return self.hits


class FakeChunkRepository:
    def __init__(
        self,
        *,
        chunks: dict[int, StoredChunk],
    ) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, Any]] = []

    async def get_by_ids(
        self,
        *,
        chunk_ids: list[int],
        workspace_id: int,
    ) -> dict[int, StoredChunk]:
        self.calls.append(
            {
                "chunk_ids": chunk_ids,
                "workspace_id": workspace_id,
            }
        )
        return self.chunks


class FakeLLMProvider:
    def __init__(
        self,
        *,
        result: LLMAnswer,
    ) -> None:
        self.result = result
        self.calls: list[dict[str, str]] = []

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMAnswer:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        return self.result


def make_hit(
    *,
    point_id: int = 101,
    score: float = 0.91,
) -> VectorSearchHit:
    return VectorSearchHit(
        point_id=point_id,
        score=score,
        payload=ChunkVectorPayload(
            workspace_id=1,
            document_id=10,
            chunk_id=f"payload-{point_id}",
            chunk_index=0,
            section="Payload section",
            document_name="payload.pdf",
            source_type="pdf",
            page_start=1,
            page_end=1,
        ),
    )


def make_stored_chunk(
    *,
    chunk_db_id: int = 101,
) -> StoredChunk:
    return StoredChunk(
        chunk_db_id=chunk_db_id,
        chunk_id=f"stored-{chunk_db_id}",
        document_id=20,
        document_name="postgresql.pdf",
        source_type="pdf",
        chunk_index=3,
        section="Transactions",
        page_start=4,
        page_end=5,
        text="PostgreSQL uses MVCC for concurrency control.",
    )


def make_service(
    *,
    hits: list[VectorSearchHit],
    chunks: dict[int, StoredChunk],
    llm_answer: LLMAnswer,
) -> tuple[
    AnswerService,
    FakeRetrievalService,
    FakeChunkRepository,
    FakeLLMProvider,
]:
    retrieval_service = FakeRetrievalService(hits=hits)
    chunk_repository = FakeChunkRepository(chunks=chunks)
    llm_provider = FakeLLMProvider(result=llm_answer)

    service = AnswerService(
        retrieval_service=retrieval_service,  # type: ignore[arg-type]
        chunk_repository=chunk_repository,  # type: ignore[arg-type]
        context_enricher=ContextEnricher(),
        context_builder=ContextBuilder(max_characters=10_000),
        llm_provider=llm_provider,
    )

    return (
        service,
        retrieval_service,
        chunk_repository,
        llm_provider,
    )


@pytest.mark.asyncio
async def test_answer_builds_server_verified_citation() -> None:
    service, _, _, llm_provider = make_service(
        hits=[make_hit()],
        chunks={101: make_stored_chunk()},
        llm_answer=LLMAnswer(
            text="PostgreSQL 使用 MVCC。",
            cited_source_ids=("SOURCE_1",),
            refused=False,
        ),
    )

    result = await service.answer(
        question=" PostgreSQL 如何处理并发？ ",
        workspace_id=1,
    )

    assert result.question == "PostgreSQL 如何处理并发？"
    assert result.text == "PostgreSQL 使用 MVCC。"
    assert result.refused is False
    assert len(result.citations) == 1

    citation = result.citations[0]

    assert citation.chunk_id == "stored-101"
    assert citation.document_id == 20
    assert citation.document_name == "postgresql.pdf"
    assert citation.page_start == 4
    assert citation.page_end == 5
    assert citation.section == "Transactions"
    assert citation.quote == (
        "PostgreSQL uses MVCC for concurrency control."
    )

    assert "[SOURCE_1]" in llm_provider.calls[0]["user_prompt"]
    assert "Answer only from the supplied sources" in (
        llm_provider.calls[0]["system_prompt"]
    )


@pytest.mark.asyncio
async def test_answer_refuses_without_retrieval_hits() -> None:
    service, _, chunk_repository, llm_provider = make_service(
        hits=[],
        chunks={},
        llm_answer=LLMAnswer(
            text="Unused",
            cited_source_ids=(),
            refused=False,
        ),
    )

    result = await service.answer(
        question="Unknown question",
        workspace_id=1,
    )

    assert result.text == REFUSAL_TEXT
    assert result.citations == ()
    assert result.refused is True
    assert chunk_repository.calls == []
    assert llm_provider.calls == []


@pytest.mark.asyncio
async def test_answer_raises_when_all_hits_are_missing() -> None:
    service, _, _, llm_provider = make_service(
        hits=[make_hit()],
        chunks={},
        llm_answer=LLMAnswer(
            text="Unused",
            cited_source_ids=(),
            refused=False,
        ),
    )

    with pytest.raises(
        AnsweringDataConsistencyError,
        match="all retrieved chunks are missing from PostgreSQL",
    ):
        await service.answer(
            question="Question",
            workspace_id=1,
        )

    assert llm_provider.calls == []


@pytest.mark.asyncio
async def test_answer_refuses_when_llm_refuses() -> None:
    service, _, _, _ = make_service(
        hits=[make_hit()],
        chunks={101: make_stored_chunk()},
        llm_answer=LLMAnswer(
            text="Insufficient evidence",
            cited_source_ids=(),
            refused=True,
        ),
    )

    result = await service.answer(
        question="Question",
        workspace_id=1,
    )

    assert result.text == REFUSAL_TEXT
    assert result.citations == ()
    assert result.refused is True


@pytest.mark.asyncio
async def test_answer_rejects_unknown_llm_source() -> None:
    service, _, _, _ = make_service(
        hits=[make_hit()],
        chunks={101: make_stored_chunk()},
        llm_answer=LLMAnswer(
            text="Unsupported answer",
            cited_source_ids=("SOURCE_99",),
            refused=False,
        ),
    )

    with pytest.raises(
        InvalidLLMCitationError,
        match="LLM cited unknown sources: SOURCE_99",
    ):
        await service.answer(
            question="Question",
            workspace_id=1,
        )


@pytest.mark.asyncio
async def test_answer_deduplicates_repeated_citations() -> None:
    service, _, _, _ = make_service(
        hits=[make_hit()],
        chunks={101: make_stored_chunk()},
        llm_answer=LLMAnswer(
            text="Answer",
            cited_source_ids=("SOURCE_1", "SOURCE_1"),
            refused=False,
        ),
    )

    result = await service.answer(
        question="Question",
        workspace_id=1,
    )

    assert len(result.citations) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "workspace_id", "retrieval_limit", "message"),
    [
        ("", 1, 5, "question must not be empty"),
        ("   ", 1, 5, "question must not be empty"),
        ("Question", 0, 5, "workspace_id must be greater than zero"),
        ("Question", -1, 5, "workspace_id must be greater than zero"),
        ("Question", 1, 0, "retrieval_limit must be greater than zero"),
        ("Question", 1, -1, "retrieval_limit must be greater than zero"),
    ],
)
async def test_answer_rejects_invalid_arguments(
    question: str,
    workspace_id: int,
    retrieval_limit: int,
    message: str,
) -> None:
    service, retrieval_service, _, _ = make_service(
        hits=[],
        chunks={},
        llm_answer=LLMAnswer(
            text="Unused",
            cited_source_ids=(),
            refused=False,
        ),
    )

    with pytest.raises(ValueError, match=message):
        await service.answer(
            question=question,
            workspace_id=workspace_id,
            retrieval_limit=retrieval_limit,
        )

    assert retrieval_service.calls == []
