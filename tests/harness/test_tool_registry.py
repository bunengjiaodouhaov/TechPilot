from pydantic import BaseModel

from app.harness.tool_registry import (
    DuplicateToolError,
    ToolNotFoundError,
    ToolRegistry,
)
from app.harness.tool_runtime import ToolRiskLevel


class EmptySchema(BaseModel):
    pass


class FakeTool:
    description = "Fake test tool."
    input_schema = EmptySchema
    output_schema = EmptySchema
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 1.0
    max_retries = 0

    def __init__(self, name: str) -> None:
        self.name = name

    async def execute(self, tool_input: BaseModel) -> BaseModel:
        return EmptySchema()


def test_registry_registers_and_gets_tool() -> None:
    registry = ToolRegistry()
    tool = FakeTool("read_file")

    registry.register(tool)

    assert registry.get("read_file") is tool


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry()

    registry.register(FakeTool("read_file"))

    try:
        registry.register(FakeTool("read_file"))
    except DuplicateToolError:
        pass
    else:
        raise AssertionError("duplicate tool name was accepted")


def test_registry_rejects_unknown_tool() -> None:
    registry = ToolRegistry()

    try:
        registry.get("missing")
    except ToolNotFoundError:
        pass
    else:
        raise AssertionError("unknown tool was accepted")


def test_registry_lists_names_deterministically() -> None:
    registry = ToolRegistry()

    registry.register(FakeTool("search_code"))
    registry.register(FakeTool("read_file"))
    registry.register(FakeTool("tree"))

    assert registry.list_names() == (
        "read_file",
        "search_code",
        "tree",
    )
