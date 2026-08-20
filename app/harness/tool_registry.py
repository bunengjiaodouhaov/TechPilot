from __future__ import annotations

from app.harness.tool_runtime import ToolContract


class ToolRegistryError(ValueError):
    """Base error for tool registry operations."""


class DuplicateToolError(ToolRegistryError):
    """Raised when a tool name is already registered."""


class ToolNotFoundError(ToolRegistryError):
    """Raised when a requested tool is not registered."""


class ToolRegistry:
    """Minimal deterministic registry for tool contracts."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolContract] = {}

    def register(self, tool: ToolContract) -> None:
        if not tool.name.strip():
            raise ValueError("tool name must not be empty")

        if tool.name in self._tools:
            raise DuplicateToolError(
                f"tool already registered: {tool.name}"
            )

        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolContract:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(
                f"tool not registered: {name}"
            ) from exc

    def list_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))
