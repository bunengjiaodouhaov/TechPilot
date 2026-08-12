import pytest

from app.repository.ast_service import PythonSymbolKind
from app.repository.code_hybrid import (
    CodeHybridIdentityError,
    CodeHybridRetrievalService,
)
from app.repository.code_index import CodeSearchHit


def make_hit(
    *,
    chunk_id: str,
    symbol: str,
    score: float,
    channel: str,
    file_path: str = "app/service.py",
    line_start: int = 1,
    line_end: int = 2,
) -> CodeSearchHit:
    return CodeSearchHit(
        chunk_id=chunk_id,
        file_path=file_path,
        symbol=symbol,
        kind=PythonSymbolKind.FUNCTION,
        line_start=line_start,
        line_end=line_end,
        score=score,
        channel=channel,
    )


class FakeCodeRetrievalService:
    def __init__(
        self,
        *,
        keyword_hits: list[CodeSearchHit],
        dense_hits: list[CodeSearchHit],
    ) -> None:
        self.keyword_hits = keyword_hits
        self.dense_hits = dense_hits
        self.calls: list[tuple[str, int]] = []

    async def search_keyword(self, *, query: str, limit: int):
        self.calls.append(("keyword", limit))
        return self.keyword_hits[:limit]

    async def search_dense(self, *, query: str, limit: int):
        self.calls.append(("dense", limit))
        return self.dense_hits[:limit]


@pytest.mark.asyncio
async def test_hybrid_rewards_consensus_without_comparing_raw_scores() -> None:
    shared_keyword = make_hit(
        chunk_id="shared",
        symbol="shared",
        score=0.01,
        channel="keyword",
    )
    shared_dense = make_hit(
        chunk_id="shared",
        symbol="shared",
        score=0.99,
        channel="dense",
    )
    retrieval = FakeCodeRetrievalService(
        keyword_hits=[
            make_hit(
                chunk_id="keyword-only",
                symbol="keyword_only",
                score=999.0,
                channel="keyword",
                line_start=3,
                line_end=4,
            ),
            shared_keyword,
        ],
        dense_hits=[
            make_hit(
                chunk_id="dense-only",
                symbol="dense_only",
                score=0.9999,
                channel="dense",
                line_start=5,
                line_end=6,
            ),
            shared_dense,
        ],
    )
    service = CodeHybridRetrievalService(
        retrieval_service=retrieval,
        rrf_k=60,
    )

    hits = await service.search(query="find shared behavior", limit=3)

    assert hits[0].chunk_id == "shared"
    assert hits[0].keyword_rank == 2
    assert hits[0].dense_rank == 2
    assert hits[0].keyword_score == 0.01
    assert hits[0].dense_score == 0.99
    assert all(limit >= 12 for _, limit in retrieval.calls)


@pytest.mark.asyncio
async def test_hybrid_keeps_single_channel_candidates() -> None:
    retrieval = FakeCodeRetrievalService(
        keyword_hits=[
            make_hit(
                chunk_id="keyword-only",
                symbol="keyword_only",
                score=2.0,
                channel="keyword",
            )
        ],
        dense_hits=[],
    )
    service = CodeHybridRetrievalService(retrieval_service=retrieval)

    hits = await service.search(query="keyword only", limit=5)

    assert [hit.chunk_id for hit in hits] == ["keyword-only"]
    assert hits[0].keyword_rank == 1
    assert hits[0].dense_rank is None


@pytest.mark.asyncio
async def test_hybrid_rejects_identity_disagreement_for_same_chunk_id() -> None:
    retrieval = FakeCodeRetrievalService(
        keyword_hits=[
            make_hit(
                chunk_id="same-id",
                symbol="first",
                score=1.0,
                channel="keyword",
            )
        ],
        dense_hits=[
            make_hit(
                chunk_id="same-id",
                symbol="second",
                score=1.0,
                channel="dense",
            )
        ],
    )
    service = CodeHybridRetrievalService(retrieval_service=retrieval)

    with pytest.raises(CodeHybridIdentityError):
        await service.search(query="identity", limit=5)


@pytest.mark.asyncio
async def test_hybrid_validates_query_and_limit() -> None:
    service = CodeHybridRetrievalService(
        retrieval_service=FakeCodeRetrievalService(
            keyword_hits=[],
            dense_hits=[],
        )
    )

    with pytest.raises(ValueError, match="query"):
        await service.search(query=" ", limit=5)

    with pytest.raises(ValueError, match="limit"):
        await service.search(query="valid", limit=0)
