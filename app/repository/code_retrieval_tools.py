from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.harness.tool_runtime import ToolRiskLevel
from app.repository.ast_service import PythonSymbolKind
from app.repository.code_retrieval import CodeRetrievalService
from app.repository.code_hybrid import (
    CodeHybridRetrievalService,
    CodeHybridSearchHit,
)


class CodeRetrievalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    limit: int = Field(default=10, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized


class CodeRetrievalMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    path: str
    symbol: str
    kind: PythonSymbolKind
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    score: float


class CodeRetrievalOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    matches: list[CodeRetrievalMatch]
    match_count: int = Field(ge=0)
    truncated: bool = False


class CodeHybridRetrievalMatch(CodeRetrievalMatch):
    keyword_rank: int | None = Field(default=None, ge=1)
    dense_rank: int | None = Field(default=None, ge=1)
    keyword_score: float | None = None
    dense_score: float | None = None


class CodeHybridRetrievalOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    matches: list[CodeHybridRetrievalMatch]
    match_count: int = Field(ge=0)
    truncated: bool = False


class SearchCodeKeywordTool:
    name = "search_code_keyword"
    description = "Retrieve code candidates with the repository keyword index."
    input_schema = CodeRetrievalInput
    output_schema = CodeRetrievalOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 5.0
    max_retries = 0

    def __init__(
        self,
        *,
        service: CodeRetrievalService,
    ) -> None:
        self._service = service

    async def execute(
        self,
        tool_input: CodeRetrievalInput,
    ) -> CodeRetrievalOutput:
        hits = await self._service.search_keyword(
            query=tool_input.query,
            limit=tool_input.limit + 1,
        )
        return _build_output(
            query=tool_input.query,
            hits=hits,
            limit=tool_input.limit,
        )


class SearchCodeDenseTool:
    name = "search_code_dense"
    description = "Retrieve code candidates with semantic embeddings."
    input_schema = CodeRetrievalInput
    output_schema = CodeRetrievalOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 30.0
    max_retries = 0

    def __init__(
        self,
        *,
        service: CodeRetrievalService,
    ) -> None:
        self._service = service

    async def execute(
        self,
        tool_input: CodeRetrievalInput,
    ) -> CodeRetrievalOutput:
        hits = await self._service.search_dense(
            query=tool_input.query,
            limit=tool_input.limit + 1,
        )
        return _build_output(
            query=tool_input.query,
            hits=hits,
            limit=tool_input.limit,
        )


class SearchCodeHybridTool:
    name = "search_code_hybrid"
    description = "Fuse keyword and dense code candidates with reciprocal-rank fusion."
    input_schema = CodeRetrievalInput
    output_schema = CodeHybridRetrievalOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 30.0
    max_retries = 0

    def __init__(
        self,
        *,
        service: CodeHybridRetrievalService,
    ) -> None:
        self._service = service

    async def execute(
        self,
        tool_input: CodeRetrievalInput,
    ) -> CodeHybridRetrievalOutput:
        hits = await self._service.search(
            query=tool_input.query,
            limit=tool_input.limit + 1,
        )
        truncated = len(hits) > tool_input.limit
        selected = hits[: tool_input.limit]
        matches = [
            _build_hybrid_match(hit)
            for hit in selected
        ]
        return CodeHybridRetrievalOutput(
            query=tool_input.query,
            matches=matches,
            match_count=len(matches),
            truncated=truncated,
        )


def _build_output(
    *,
    query: str,
    hits: list,
    limit: int,
) -> CodeRetrievalOutput:
    truncated = len(hits) > limit
    selected = hits[:limit]
    matches = [
        CodeRetrievalMatch(
            chunk_id=hit.chunk_id,
            path=hit.file_path,
            symbol=hit.symbol,
            kind=hit.kind,
            line_start=hit.line_start,
            line_end=hit.line_end,
            score=hit.score,
        )
        for hit in selected
    ]

    return CodeRetrievalOutput(
        query=query,
        matches=matches,
        match_count=len(matches),
        truncated=truncated,
    )


def _build_hybrid_match(
    hit: CodeHybridSearchHit,
) -> CodeHybridRetrievalMatch:
    return CodeHybridRetrievalMatch(
        chunk_id=hit.chunk_id,
        path=hit.file_path,
        symbol=hit.symbol,
        kind=hit.kind,
        line_start=hit.line_start,
        line_end=hit.line_end,
        score=hit.score,
        keyword_rank=hit.keyword_rank,
        dense_rank=hit.dense_rank,
        keyword_score=hit.keyword_score,
        dense_score=hit.dense_score,
    )
