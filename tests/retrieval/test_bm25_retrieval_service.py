import pytest

from app.retrieval.bm25_dto import BM25Chunk
from app.retrieval.bm25_retrieval_service import BM25RetrievalService


class FakeBM25ChunkRepository:
    def __init__(self, chunks: list[BM25Chunk]) -> None:
        self.chunks = chunks
        self.workspace_ids: list[int] = []

    async def list_searchable(
        self,
        *,
        workspace_id: int,
    ) -> list[BM25Chunk]:
        self.workspace_ids.append(workspace_id)

        return [
            chunk
            for chunk in self.chunks
            if chunk.workspace_id == workspace_id
        ]


def make_chunk(
    *,
    point_id: int,
    chunk_id: str,
    text: str,
    workspace_id: int = 1,
) -> BM25Chunk:
    return BM25Chunk(
        point_id=point_id,
        workspace_id=workspace_id,
        document_id=point_id,
        chunk_id=chunk_id,
        chunk_index=0,
        section=None,
        document_name=f"document-{point_id}.md",
        source_type="markdown",
        page_start=None,
        page_end=None,
        text=text,
    )


@pytest.mark.asyncio
async def test_search_ranks_exact_technical_term_match_first() -> None:
    repository = FakeBM25ChunkRepository(
        [
            make_chunk(
                point_id=1,
                chunk_id="chunk-1",
                text="UploadFile uses SpooledTemporaryFile and disk storage.",
            ),
            make_chunk(
                point_id=2,
                chunk_id="chunk-2",
                text="FastAPI supports dependency injection.",
            ),
        ]
    )

    service = BM25RetrievalService(
        chunk_repository=repository,
    )

    hits = await service.search(
        query="UploadFile SpooledTemporaryFile",
        workspace_id=1,
        limit=5,
    )

    assert [hit.chunk_id for hit in hits] == ["chunk-1"]
    assert hits[0].score > 0


@pytest.mark.asyncio
async def test_search_respects_workspace_corpus() -> None:
    repository = FakeBM25ChunkRepository(
        [
            make_chunk(
                point_id=1,
                chunk_id="workspace-1",
                text="UploadFile",
                workspace_id=1,
            ),
            make_chunk(
                point_id=2,
                chunk_id="workspace-2",
                text="UploadFile",
                workspace_id=2,
            ),
        ]
    )

    service = BM25RetrievalService(
        chunk_repository=repository,
    )

    hits = await service.search(
        query="UploadFile",
        workspace_id=1,
    )

    assert [hit.chunk_id for hit in hits] == ["workspace-1"]
    assert repository.workspace_ids == [1]


@pytest.mark.asyncio
async def test_search_returns_no_hits_without_lexical_overlap() -> None:
    repository = FakeBM25ChunkRepository(
        [
            make_chunk(
                point_id=1,
                chunk_id="chunk-1",
                text="stored on disk",
            ),
        ]
    )

    service = BM25RetrievalService(
        chunk_repository=repository,
    )

    hits = await service.search(
        query="内存阈值",
        workspace_id=1,
    )

    assert hits == []


@pytest.mark.asyncio
async def test_search_obeys_limit() -> None:
    repository = FakeBM25ChunkRepository(
        [
            make_chunk(
                point_id=1,
                chunk_id="chunk-1",
                text="FastAPI UploadFile",
            ),
            make_chunk(
                point_id=2,
                chunk_id="chunk-2",
                text="FastAPI UploadFile",
            ),
        ]
    )

    service = BM25RetrievalService(
        chunk_repository=repository,
    )

    hits = await service.search(
        query="FastAPI",
        workspace_id=1,
        limit=1,
    )

    assert len(hits) == 1


@pytest.mark.asyncio
async def test_search_validates_arguments() -> None:
    service = BM25RetrievalService(
        chunk_repository=FakeBM25ChunkRepository([]),
    )

    with pytest.raises(ValueError, match="query"):
        await service.search(
            query=" ",
            workspace_id=1,
        )

    with pytest.raises(ValueError, match="workspace_id"):
        await service.search(
            query="FastAPI",
            workspace_id=0,
        )

    with pytest.raises(ValueError, match="limit"):
        await service.search(
            query="FastAPI",
            workspace_id=1,
            limit=0,
        )
