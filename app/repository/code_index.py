from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal, Sequence

from app.repository.ast_service import PythonAstService, PythonSymbolKind


_IDENTIFIER_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_\.]*|\d+(?:\.\d+)*"
)
_CAMEL_RE = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+"
)


@dataclass(frozen=True, slots=True)
class CodeChunk:
    chunk_id: str
    file_path: str
    symbol: str
    kind: PythonSymbolKind
    line_start: int
    line_end: int
    content: str

    @property
    def embedding_text(self) -> str:
        return (
            f"path: {self.file_path}\n"
            f"symbol: {self.symbol}\n"
            f"kind: {self.kind.value}\n"
            f"code:\n{self.content}"
        )


@dataclass(frozen=True, slots=True)
class CodeSearchHit:
    chunk_id: str
    file_path: str
    symbol: str
    kind: PythonSymbolKind
    line_start: int
    line_end: int
    score: float
    channel: Literal["keyword", "dense"]


class PythonSymbolCodeChunker:
    """Build function/class/method chunks from authoritative Python source."""

    def __init__(
        self,
        *,
        ast_service: PythonAstService | None = None,
    ) -> None:
        self._ast_service = ast_service or PythonAstService()

    def chunk_source(
        self,
        *,
        file_path: str,
        source: str,
    ) -> list[CodeChunk]:
        symbols = self._ast_service.list_symbols_from_source(
            source,
            filename=file_path,
        )
        lines = source.splitlines()
        chunks: list[CodeChunk] = []

        for symbol in symbols:
            content = "\n".join(
                lines[symbol.line_start - 1 : symbol.line_end]
            )
            chunks.append(
                CodeChunk(
                    chunk_id=self._stable_chunk_id(
                        file_path=file_path,
                        symbol=symbol.qualified_name,
                        kind=symbol.kind,
                        line_start=symbol.line_start,
                        line_end=symbol.line_end,
                        content=content,
                    ),
                    file_path=file_path,
                    symbol=symbol.qualified_name,
                    kind=symbol.kind,
                    line_start=symbol.line_start,
                    line_end=symbol.line_end,
                    content=content,
                )
            )

        return chunks

    @staticmethod
    def _stable_chunk_id(
        *,
        file_path: str,
        symbol: str,
        kind: PythonSymbolKind,
        line_start: int,
        line_end: int,
        content: str,
    ) -> str:
        identity = "\0".join(
            (
                file_path,
                symbol,
                kind.value,
                str(line_start),
                str(line_end),
                content,
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def tokenize_code_text(text: str) -> list[str]:
    """Expand code identifiers so natural words can match snake/camel names."""

    tokens: list[str] = []

    for match in _IDENTIFIER_RE.finditer(text):
        raw = match.group(0)
        tokens.append(raw.lower())

        for dotted_part in raw.split("."):
            for snake_part in dotted_part.split("_"):
                if not snake_part:
                    continue

                normalized = snake_part.lower()
                tokens.append(normalized)

                for camel_part in _CAMEL_RE.findall(snake_part):
                    camel_normalized = camel_part.lower()
                    if (
                        camel_normalized
                        and camel_normalized != normalized
                    ):
                        tokens.append(camel_normalized)

    return tokens


class InMemoryCodeKeywordIndex:
    """Process-local lexical index for code chunks."""

    def __init__(self) -> None:
        self._chunks: tuple[CodeChunk, ...] = ()
        self._term_frequencies: tuple[Counter[str], ...] = ()
        self._document_frequencies: dict[str, int] = {}

    def replace(self, chunks: Sequence[CodeChunk]) -> None:
        stored = tuple(chunks)
        term_frequencies = tuple(
            Counter(tokenize_code_text(chunk.embedding_text))
            for chunk in stored
        )
        document_frequencies: defaultdict[str, int] = defaultdict(int)

        for frequencies in term_frequencies:
            for term in frequencies:
                document_frequencies[term] += 1

        self._chunks = stored
        self._term_frequencies = term_frequencies
        self._document_frequencies = dict(document_frequencies)

    def search(
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
        if not self._chunks:
            return []

        query_terms = tuple(
            dict.fromkeys(tokenize_code_text(normalized_query))
        )
        scored: list[tuple[float, CodeChunk]] = []
        document_count = len(self._chunks)

        for chunk, frequencies in zip(
            self._chunks,
            self._term_frequencies,
            strict=True,
        ):
            score = 0.0

            for term in query_terms:
                frequency = frequencies.get(term, 0)
                if frequency == 0:
                    continue

                document_frequency = self._document_frequencies[term]
                idf = math.log(
                    1.0
                    + (document_count + 1.0)
                    / (document_frequency + 1.0)
                )
                score += idf * (1.0 + math.log(frequency))

            if score > 0:
                scored.append((score, chunk))

        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].file_path,
                item[1].line_start,
                item[1].symbol,
            )
        )

        return [
            _to_hit(
                chunk=chunk,
                score=score,
                channel="keyword",
            )
            for score, chunk in scored[:limit]
        ]


class InMemoryCodeDenseIndex:
    """Process-local semantic index; persistent storage is a later adapter."""

    def __init__(self) -> None:
        self._chunks: tuple[CodeChunk, ...] = ()
        self._vectors: tuple[tuple[float, ...], ...] = ()
        self._dimension: int | None = None

    def replace(
        self,
        *,
        chunks: Sequence[CodeChunk],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunk/vector count mismatch")

        if not chunks:
            self._chunks = ()
            self._vectors = ()
            self._dimension = None
            return

        dimension = len(vectors[0])
        if dimension <= 0:
            raise ValueError("vector dimension must be greater than zero")

        normalized_vectors: list[tuple[float, ...]] = []
        for vector in vectors:
            if len(vector) != dimension:
                raise ValueError("vector dimension mismatch")
            normalized_vectors.append(
                tuple(float(value) for value in vector)
            )

        self._chunks = tuple(chunks)
        self._vectors = tuple(normalized_vectors)
        self._dimension = dimension

    def search(
        self,
        *,
        query_vector: Sequence[float],
        limit: int = 10,
    ) -> list[CodeSearchHit]:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if not self._chunks:
            return []
        if (
            self._dimension is None
            or len(query_vector) != self._dimension
        ):
            raise ValueError("query vector dimension mismatch")

        query = tuple(float(value) for value in query_vector)
        scored = [
            (
                _cosine_similarity(query, vector),
                chunk,
            )
            for chunk, vector in zip(
                self._chunks,
                self._vectors,
                strict=True,
            )
        ]
        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].file_path,
                item[1].line_start,
                item[1].symbol,
            )
        )

        return [
            _to_hit(
                chunk=chunk,
                score=score,
                channel="dense",
            )
            for score, chunk in scored[:limit]
        ]


def _cosine_similarity(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))

    if left_norm == 0 or right_norm == 0:
        return 0.0

    return sum(
        a * b
        for a, b in zip(left, right, strict=True)
    ) / (left_norm * right_norm)


def _to_hit(
    *,
    chunk: CodeChunk,
    score: float,
    channel: Literal["keyword", "dense"],
) -> CodeSearchHit:
    return CodeSearchHit(
        chunk_id=chunk.chunk_id,
        file_path=chunk.file_path,
        symbol=chunk.symbol,
        kind=chunk.kind,
        line_start=chunk.line_start,
        line_end=chunk.line_end,
        score=float(score),
        channel=channel,
    )
