from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.harness.tool_runtime import ToolRiskLevel
from app.repository.ast_service import (
    PythonAstParseError,
    PythonAstService,
    PythonSymbolKind,
)
from app.repository.read_boundary import RepositoryReadBoundary


class TreeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TreeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[str]
    file_count: int


class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)


class ReadFileOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str
    size_bytes: int


class TreeTool:
    name = "tree"
    description = "List readable repository files in deterministic order."
    input_schema = TreeInput
    output_schema = TreeOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 5.0
    max_retries = 0

    def __init__(self, boundary: RepositoryReadBoundary) -> None:
        self._boundary = boundary

    async def execute(self, tool_input: TreeInput) -> TreeOutput:
        files = self._boundary.list_files()

        return TreeOutput(
            files=files,
            file_count=len(files),
        )


class ReadFileTool:
    name = "read_file"
    description = "Read one safe UTF-8 repository text file."
    input_schema = ReadFileInput
    output_schema = ReadFileOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 5.0
    max_retries = 0

    def __init__(self, boundary: RepositoryReadBoundary) -> None:
        self._boundary = boundary

    async def execute(
        self,
        tool_input: ReadFileInput,
    ) -> ReadFileOutput:
        resolved = self._boundary.resolve_file(tool_input.path)
        content = resolved.read_text(encoding="utf-8")

        return ReadFileOutput(
            path=resolved.relative_to(
                self._boundary.root
            ).as_posix(),
            content=content,
            size_bytes=resolved.stat().st_size,
        )


class SearchCodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    limit: int = Field(default=20, ge=1, le=100)
    case_sensitive: bool = False

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized


class SearchCodeMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    line_number: int
    line: str


class SearchCodeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    matches: list[SearchCodeMatch]
    match_count: int
    truncated: bool


class SearchCodeTool:
    name = "search_code"
    description = "Search readable repository text with literal matching."
    input_schema = SearchCodeInput
    output_schema = SearchCodeOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 5.0
    max_retries = 0

    def __init__(self, boundary: RepositoryReadBoundary) -> None:
        self._boundary = boundary

    async def execute(
        self,
        tool_input: SearchCodeInput,
    ) -> SearchCodeOutput:
        needle = (
            tool_input.query
            if tool_input.case_sensitive
            else tool_input.query.casefold()
        )
        matches: list[SearchCodeMatch] = []

        for relative_path in self._boundary.list_files():
            resolved = self._boundary.resolve_file(relative_path)
            content = resolved.read_text(encoding="utf-8")

            for line_number, line in enumerate(
                content.splitlines(),
                start=1,
            ):
                haystack = (
                    line
                    if tool_input.case_sensitive
                    else line.casefold()
                )

                if needle not in haystack:
                    continue

                if len(matches) >= tool_input.limit:
                    return SearchCodeOutput(
                        query=tool_input.query,
                        matches=matches,
                        match_count=len(matches),
                        truncated=True,
                    )

                matches.append(
                    SearchCodeMatch(
                        path=relative_path,
                        line_number=line_number,
                        line=line,
                    )
                )

        return SearchCodeOutput(
            query=tool_input.query,
            matches=matches,
            match_count=len(matches),
            truncated=False,
        )


class SearchSymbolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized


class SearchSymbolMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    name: str
    qualified_name: str
    kind: PythonSymbolKind
    line_start: int
    line_end: int


class SearchSymbolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    matches: list[SearchSymbolMatch]
    match_count: int
    parse_error_count: int
    truncated: bool


class SearchSymbolTool:
    name = "search_symbol"
    description = "Find Python class, function, and method definitions."
    input_schema = SearchSymbolInput
    output_schema = SearchSymbolOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 5.0
    max_retries = 0

    def __init__(
        self,
        boundary: RepositoryReadBoundary,
        ast_service: PythonAstService | None = None,
    ) -> None:
        self._boundary = boundary
        self._ast_service = ast_service or PythonAstService()

    async def execute(
        self,
        tool_input: SearchSymbolInput,
    ) -> SearchSymbolOutput:
        needle = tool_input.query.casefold()
        matches: list[SearchSymbolMatch] = []
        parse_error_count = 0

        for relative_path in self._boundary.list_files():
            if not relative_path.endswith(".py"):
                continue

            resolved = self._boundary.resolve_file(relative_path)

            try:
                symbols = self._ast_service.list_symbols(resolved)
            except PythonAstParseError:
                parse_error_count += 1
                continue

            for symbol in symbols:
                if needle not in {
                    symbol.name.casefold(),
                    symbol.qualified_name.casefold(),
                }:
                    continue

                if len(matches) >= tool_input.limit:
                    return SearchSymbolOutput(
                        query=tool_input.query,
                        matches=matches,
                        match_count=len(matches),
                        parse_error_count=parse_error_count,
                        truncated=True,
                    )

                matches.append(
                    SearchSymbolMatch(
                        path=relative_path,
                        name=symbol.name,
                        qualified_name=symbol.qualified_name,
                        kind=symbol.kind,
                        line_start=symbol.line_start,
                        line_end=symbol.line_end,
                    )
                )

        return SearchSymbolOutput(
            query=tool_input.query,
            matches=matches,
            match_count=len(matches),
            parse_error_count=parse_error_count,
            truncated=False,
        )
