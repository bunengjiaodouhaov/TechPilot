from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.repository.ast_service import PythonAstParseError
from app.repository.code_index import (
    CodeSearchHit,
    InMemoryCodeDenseIndex,
    InMemoryCodeKeywordIndex,
    PythonSymbolCodeChunker,
)
from app.repository.read_boundary import RepositoryReadBoundary
from app.retrieval.embedding import EmbeddingProvider


@dataclass(frozen=True, slots=True)
class CodeIndexBuildReport:
    python_file_count: int
    chunk_count: int
    parse_error_count: int
    read_error_count: int


class CodeIndexingService:
    """Build code indexes from files allowed by RepositoryReadBoundary."""

    def __init__(
        self,
        *,
        boundary: RepositoryReadBoundary,
        embedding_provider: EmbeddingProvider,
        keyword_index: InMemoryCodeKeywordIndex,
        dense_index: InMemoryCodeDenseIndex,
        chunker: PythonSymbolCodeChunker | None = None,
    ) -> None:
        self._boundary = boundary
        self._embedding_provider = embedding_provider
        self._keyword_index = keyword_index
        self._dense_index = dense_index
        self._chunker = chunker or PythonSymbolCodeChunker()

    async def rebuild(self) -> CodeIndexBuildReport:
        chunks = []
        python_file_count = 0
        parse_error_count = 0
        read_error_count = 0

        for file_path in self._boundary.list_files():
            if not file_path.endswith(".py"):
                continue

            python_file_count += 1

            try:
                resolved = self._boundary.resolve_file(file_path)
                source = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, ValueError):
                read_error_count += 1
                continue

            try:
                chunks.extend(
                    self._chunker.chunk_source(
                        file_path=file_path,
                        source=source,
                    )
                )
            except PythonAstParseError:
                parse_error_count += 1

        vectors = []
        if chunks:
            vectors = await asyncio.to_thread(
                self._embedding_provider.embed_documents,
                [chunk.embedding_text for chunk in chunks],
            )

        # Update both channels only after embedding succeeds.
        self._keyword_index.replace(chunks)
        self._dense_index.replace(
            chunks=chunks,
            vectors=vectors,
        )

        return CodeIndexBuildReport(
            python_file_count=python_file_count,
            chunk_count=len(chunks),
            parse_error_count=parse_error_count,
            read_error_count=read_error_count,
        )


class CodeRetrievalService:
    """Expose independent keyword and dense code candidate retrieval."""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        keyword_index: InMemoryCodeKeywordIndex,
        dense_index: InMemoryCodeDenseIndex,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._keyword_index = keyword_index
        self._dense_index = dense_index

    async def search_keyword(
        self,
        *,
        query: str,
        limit: int = 10,
    ) -> list[CodeSearchHit]:
        return self._keyword_index.search(
            query=query,
            limit=limit,
        )

    async def search_dense(
        self,
        *,
        query: str,
        limit: int = 10,
    ) -> list[CodeSearchHit]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        vector = await asyncio.to_thread(
            self._embedding_provider.embed_query,
            normalized_query,
        )

        return self._dense_index.search(
            query_vector=vector,
            limit=limit,
        )
