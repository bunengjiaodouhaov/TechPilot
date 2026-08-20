from __future__ import annotations

import math
from collections import Counter

from app.retrieval.bm25_dto import BM25Chunk, BM25SearchHit
from app.retrieval.bm25_repository import BM25ChunkRepositoryProtocol
from app.retrieval.bm25_tokenizer import tokenize_for_bm25


class BM25RetrievalService:
    """Rank PostgreSQL chunks with BM25 lexical retrieval."""

    def __init__(
        self,
        *,
        chunk_repository: BM25ChunkRepositoryProtocol,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        if k1 <= 0:
            raise ValueError("k1 must be greater than zero")

        if not 0 <= b <= 1:
            raise ValueError("b must be between zero and one")

        self._chunk_repository = chunk_repository
        self._k1 = k1
        self._b = b

    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        limit: int = 5,
    ) -> list[BM25SearchHit]:
        normalized_query = query.strip()

        if not normalized_query:
            raise ValueError("query must not be empty")

        if workspace_id <= 0:
            raise ValueError("workspace_id must be greater than zero")

        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        query_tokens = tokenize_for_bm25(normalized_query)

        if not query_tokens:
            return []

        chunks = await self._chunk_repository.list_searchable(
            workspace_id=workspace_id,
        )

        if not chunks:
            return []

        tokenized_chunks = [
            tokenize_for_bm25(chunk.text)
            for chunk in chunks
        ]

        average_document_length = (
            sum(len(tokens) for tokens in tokenized_chunks)
            / len(tokenized_chunks)
        )

        if average_document_length == 0:
            return []

        document_frequencies = self._document_frequencies(
            tokenized_chunks=tokenized_chunks,
            query_tokens=query_tokens,
        )

        scored: list[tuple[float, BM25Chunk]] = []

        for chunk, tokens in zip(
            chunks,
            tokenized_chunks,
            strict=True,
        ):
            score = self._score_document(
                document_tokens=tokens,
                query_tokens=query_tokens,
                document_frequencies=document_frequencies,
                document_count=len(chunks),
                average_document_length=average_document_length,
            )

            if score > 0:
                scored.append((score, chunk))

        scored.sort(
            key=lambda item: (-item[0], item[1].point_id),
        )

        return [
            self._build_hit(chunk=chunk, score=score)
            for score, chunk in scored[:limit]
        ]

    @staticmethod
    def _document_frequencies(
        *,
        tokenized_chunks: list[list[str]],
        query_tokens: list[str],
    ) -> dict[str, int]:
        query_terms = set(query_tokens)

        return {
            term: sum(
                1
                for tokens in tokenized_chunks
                if term in set(tokens)
            )
            for term in query_terms
        }

    def _score_document(
        self,
        *,
        document_tokens: list[str],
        query_tokens: list[str],
        document_frequencies: dict[str, int],
        document_count: int,
        average_document_length: float,
    ) -> float:
        if not document_tokens:
            return 0.0

        term_frequencies = Counter(document_tokens)
        document_length = len(document_tokens)
        score = 0.0

        # Repeated query terms should not artificially multiply the score.
        for term in dict.fromkeys(query_tokens):
            term_frequency = term_frequencies.get(term, 0)

            if term_frequency == 0:
                continue

            document_frequency = document_frequencies[term]

            idf = math.log(
                1
                + (
                    document_count
                    - document_frequency
                    + 0.5
                )
                / (document_frequency + 0.5)
            )

            denominator = (
                term_frequency
                + self._k1
                * (
                    1
                    - self._b
                    + self._b
                    * document_length
                    / average_document_length
                )
            )

            score += (
                idf
                * term_frequency
                * (self._k1 + 1)
                / denominator
            )

        return score

    @staticmethod
    def _build_hit(
        *,
        chunk: BM25Chunk,
        score: float,
    ) -> BM25SearchHit:
        return BM25SearchHit(
            point_id=chunk.point_id,
            score=score,
            workspace_id=chunk.workspace_id,
            document_id=chunk.document_id,
            chunk_id=chunk.chunk_id,
            chunk_index=chunk.chunk_index,
            section=chunk.section,
            document_name=chunk.document_name,
            source_type=chunk.source_type,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
        )
