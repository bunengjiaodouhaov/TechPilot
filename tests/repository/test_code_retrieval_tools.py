from collections.abc import Sequence

import pytest

from app.repository.code_index import (
    InMemoryCodeDenseIndex,
    InMemoryCodeKeywordIndex,
    PythonSymbolCodeChunker,
)
from app.repository.code_retrieval import CodeRetrievalService
from app.repository.code_retrieval_tools import (
    CodeRetrievalInput,
    SearchCodeDenseTool,
    SearchCodeKeywordTool,
)


class FakeEmbeddingProvider:
    def embed_documents(
        self,
        texts: Sequence[str],
    ) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, query: str) -> list[float]:
        return [1.0, 0.0]


def make_service() -> CodeRetrievalService:
    chunks = PythonSymbolCodeChunker().chunk_source(
        file_path="app/service.py",
        source="def load_user():\n    return 'user'\n",
    )
    provider = FakeEmbeddingProvider()
    keyword_index = InMemoryCodeKeywordIndex()
    dense_index = InMemoryCodeDenseIndex()
    keyword_index.replace(chunks)
    dense_index.replace(
        chunks=chunks,
        vectors=provider.embed_documents(
            [chunk.embedding_text for chunk in chunks]
        ),
    )
    return CodeRetrievalService(
        embedding_provider=provider,
        keyword_index=keyword_index,
        dense_index=dense_index,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_cls",
    [SearchCodeKeywordTool, SearchCodeDenseTool],
)
async def test_retrieval_tools_return_candidate_metadata_not_source_text(
    tool_cls,
) -> None:
    tool = tool_cls(service=make_service())
    output = await tool.execute(
        CodeRetrievalInput(
            query="load user",
            limit=5,
        )
    )

    assert output.match_count == 1
    payload = output.matches[0].model_dump()
    assert payload["path"] == "app/service.py"
    assert payload["symbol"] == "load_user"
    assert "content" not in payload
    assert "snippet" not in payload

from app.harness.tool_runtime import ToolRuntime
from app.repository.code_hybrid import CodeHybridRetrievalService
from app.repository.code_retrieval_tools import SearchCodeHybridTool


@pytest.mark.asyncio
async def test_keyword_tool_reports_truncation_through_runtime() -> None:
    chunks = PythonSymbolCodeChunker().chunk_source(
        file_path="app/service.py",
        source=(
            "def load_user():\n    return 'user'\n\n"
            "def load_user_cache():\n    return 'cache'\n"
        ),
    )
    provider = FakeEmbeddingProvider()
    keyword_index = InMemoryCodeKeywordIndex()
    dense_index = InMemoryCodeDenseIndex()
    keyword_index.replace(chunks)
    dense_index.replace(
        chunks=chunks,
        vectors=provider.embed_documents(
            [chunk.embedding_text for chunk in chunks]
        ),
    )
    service = CodeRetrievalService(
        embedding_provider=provider,
        keyword_index=keyword_index,
        dense_index=dense_index,
    )
    result = await ToolRuntime().invoke(
        tool=SearchCodeKeywordTool(service=service),
        arguments={"query": "load user", "limit": 1},
    )

    assert result.ok is True
    assert result.truncated is True
    assert result.data is not None
    assert result.data["truncated"] is True
    assert result.data["match_count"] == 1


@pytest.mark.asyncio
async def test_hybrid_tool_returns_rank_provenance_without_source_text() -> None:
    service = make_service()
    hybrid = CodeHybridRetrievalService(retrieval_service=service)
    tool = SearchCodeHybridTool(service=hybrid)

    output = await tool.execute(
        CodeRetrievalInput(query="load user", limit=5)
    )

    assert output.match_count == 1
    payload = output.matches[0].model_dump()
    assert payload["path"] == "app/service.py"
    assert payload["keyword_rank"] == 1
    assert payload["dense_rank"] == 1
    assert "content" not in payload
    assert "snippet" not in payload
