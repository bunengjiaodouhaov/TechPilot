from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.harness.tool_runtime import ToolRiskLevel
from app.repository.ast_service import PythonSymbolKind
from app.repository.module_structure import PythonModuleStructureService


class InspectModulesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = "module structure"
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized


class ModuleDependencyMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: str
    path: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class ModuleSymbolMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: PythonSymbolKind
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class ModuleStructureMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    module: str
    internal_dependencies: list[ModuleDependencyMatch]
    symbols: list[ModuleSymbolMatch]


class InspectModulesOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = "module structure"
    modules: list[ModuleStructureMatch]
    module_count: int
    python_file_count: int
    parse_error_count: int
    read_error_count: int
    truncated: bool


class InspectModulesTool:
    name = "inspect_modules"
    description = (
        "Search a prebuilt Python module/import/symbol structure snapshot."
    )
    input_schema = InspectModulesInput
    output_schema = InspectModulesOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 5.0
    max_retries = 0

    def __init__(self, *, service: PythonModuleStructureService) -> None:
        self._service = service

    async def execute(
        self,
        tool_input: InspectModulesInput,
    ) -> InspectModulesOutput:
        report = self._service.inspect_repository(
            query=tool_input.query,
            limit=tool_input.limit,
        )

        return InspectModulesOutput(
            modules=[
                ModuleStructureMatch(
                    path=module.path,
                    module=module.module_name,
                    internal_dependencies=[
                        ModuleDependencyMatch(
                            module=dependency.module_name,
                            path=dependency.path,
                            line_start=dependency.line_start,
                            line_end=dependency.line_end,
                        )
                        for dependency in module.internal_dependencies
                    ],
                    symbols=[
                        ModuleSymbolMatch(
                            name=symbol.name,
                            kind=symbol.kind,
                            line_start=symbol.line_start,
                            line_end=symbol.line_end,
                        )
                        for symbol in module.symbols
                    ],
                )
                for module in report.modules
            ],
            module_count=report.module_count,
            python_file_count=report.python_file_count,
            parse_error_count=report.parse_error_count,
            read_error_count=report.read_error_count,
            truncated=report.truncated,
        )
