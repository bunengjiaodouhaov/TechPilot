from collections.abc import Sequence

import pytest

from app.repository.ast_service import PythonSymbolKind
from app.repository.code_index import (
    InMemoryCodeDenseIndex,
    InMemoryCodeKeywordIndex,
    PythonSymbolCodeChunker,
    tokenize_code_text,
)
from app.repository.code_retrieval import (
    CodeIndexingService,
    CodeRetrievalService,
)
from app.repository.read_boundary import RepositoryReadBoundary


class FakeEmbeddingProvider:
    def embed_documents(
        self,
        texts: Sequence[str],
    ) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self._vector(query)

    @staticmethod
    def _vector(text: str) -> list[float]:
        lowered = text.lower()
        if "load" in lowered or "user" in lowered:
            return [1.0, 0.0]
        if "delete" in lowered or "order" in lowered:
            return [0.0, 1.0]
        return [0.5, 0.5]


def test_code_tokenizer_expands_snake_and_camel_identifiers() -> None:
    tokens = tokenize_code_text(
        "UserService.load_user HTTPClient"
    )

    assert "userservice.load_user" in tokens
    assert "userservice" in tokens
    assert "user" in tokens
    assert "service" in tokens
    assert "load" in tokens
    assert "httpclient" in tokens
    assert "http" in tokens
    assert "client" in tokens


def test_python_symbol_chunker_builds_stable_exact_line_chunks() -> None:
    source = (
        "class UserService:\n"
        "    def load_user(self):\n"
        "        return 'user'\n"
    )
    chunker = PythonSymbolCodeChunker()

    first = chunker.chunk_source(
        file_path="app/service.py",
        source=source,
    )
    second = chunker.chunk_source(
        file_path="app/service.py",
        source=source,
    )

    assert [chunk.chunk_id for chunk in first] == [
        chunk.chunk_id for chunk in second
    ]
    method = next(
        chunk
        for chunk in first
        if chunk.kind == PythonSymbolKind.METHOD
    )
    assert method.symbol == "UserService.load_user"
    assert (method.line_start, method.line_end) == (2, 3)
    assert method.content == (
        "    def load_user(self):\n"
        "        return 'user'"
    )


@pytest.mark.asyncio
async def test_keyword_and_dense_share_code_chunk_identity() -> None:
    source = (
        "class UserService:\n"
        "    def load_user(self):\n"
        "        return 'user'\n\n"
        "def delete_order():\n"
        "    return None\n"
    )
    chunks = PythonSymbolCodeChunker().chunk_source(
        file_path="app/service.py",
        source=source,
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

    keyword_hits = await service.search_keyword(
        query="load user",
        limit=5,
    )
    dense_hits = await service.search_dense(
        query="find user loading",
        limit=5,
    )

    assert keyword_hits[0].symbol == "UserService.load_user"
    assert dense_hits[0].symbol in {
        "UserService",
        "UserService.load_user",
    }
    assert keyword_hits[0].channel == "keyword"
    assert dense_hits[0].channel == "dense"
    assert not hasattr(keyword_hits[0], "content")


@pytest.mark.asyncio
async def test_index_rebuild_uses_safe_python_files_and_reports_parse_errors(
    tmp_path,
) -> None:
    (tmp_path / "good.py").write_text(
        "def load_user():\n    return 'user'\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text(
        "def broken(:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "load_user docs",
        encoding="utf-8",
    )

    provider = FakeEmbeddingProvider()
    keyword_index = InMemoryCodeKeywordIndex()
    dense_index = InMemoryCodeDenseIndex()
    service = CodeIndexingService(
        boundary=RepositoryReadBoundary(tmp_path),
        embedding_provider=provider,
        keyword_index=keyword_index,
        dense_index=dense_index,
    )

    report = await service.rebuild()

    assert report.python_file_count == 2
    assert report.chunk_count == 1
    assert report.parse_error_count == 1
    assert report.read_error_count == 0
    hits = keyword_index.search(query="load user")
    assert [hit.symbol for hit in hits] == ["load_user"]
