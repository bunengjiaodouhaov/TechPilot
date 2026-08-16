from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.harness.tool_runtime import ToolRiskLevel
from app.repository.call_relationships import PythonCallRelationshipService


class InspectCallsInput(BaseModel):
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


class StaticCallMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    caller: str
    callee: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class InspectCallsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    matches: list[StaticCallMatch]
    match_count: int
    python_file_count: int
    parse_error_count: int
    read_error_count: int
    truncated: bool


class InspectCallsTool:
    name = "inspect_calls"
    description = (
        "Inspect static Python caller/callee call-site clues without "
        "claiming a complete runtime call graph."
    )
    input_schema = InspectCallsInput
    output_schema = InspectCallsOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 5.0
    max_retries = 0

    def __init__(self, *, service: PythonCallRelationshipService) -> None:
        self._service = service

    async def execute(
        self,
        tool_input: InspectCallsInput,
    ) -> InspectCallsOutput:
        report = self._service.inspect_repository(
            query=tool_input.query,
            limit=tool_input.limit,
        )
        return InspectCallsOutput(
            query=tool_input.query,
            matches=[
                StaticCallMatch(
                    path=clue.path,
                    caller=clue.caller,
                    callee=clue.callee,
                    line_start=clue.line_start,
                    line_end=clue.line_end,
                )
                for clue in report.clues
            ],
            match_count=report.match_count,
            python_file_count=report.python_file_count,
            parse_error_count=report.parse_error_count,
            read_error_count=report.read_error_count,
            truncated=report.truncated,
        )
