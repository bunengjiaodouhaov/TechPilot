from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class PythonAstParseError(ValueError):
    """Raised when a Python source file cannot be parsed."""


class PythonSymbolKind(StrEnum):
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


@dataclass(frozen=True, slots=True)
class PythonSymbol:
    name: str
    qualified_name: str
    kind: PythonSymbolKind
    line_start: int
    line_end: int


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: list[PythonSymbol] = []
        self._scope: list[tuple[str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record(node, PythonSymbolKind.CLASS)
        self._scope.append(("class", node.name))

        for child in node.body:
            self.visit(child)

        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(
        self,
        node: ast.AsyncFunctionDef,
    ) -> None:
        self._visit_function(node)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        kind = (
            PythonSymbolKind.METHOD
            if self._scope and self._scope[-1][0] == "class"
            else PythonSymbolKind.FUNCTION
        )

        self._record(node, kind)
        self._scope.append(("function", node.name))

        for child in node.body:
            self.visit(child)

        self._scope.pop()

    def _record(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: PythonSymbolKind,
    ) -> None:
        names = [name for _, name in self._scope]
        names.append(node.name)

        self.symbols.append(
            PythonSymbol(
                name=node.name,
                qualified_name=".".join(names),
                kind=kind,
                line_start=node.lineno,
                line_end=getattr(node, "end_lineno", node.lineno),
            )
        )


class PythonAstService:
    """Extract deterministic Python symbol metadata without execution."""

    def list_symbols(self, path: Path) -> list[PythonSymbol]:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            raise PythonAstParseError(
                f"unable to parse Python source: {path}"
            ) from exc

        visitor = _SymbolVisitor()
        visitor.visit(tree)

        return sorted(
            visitor.symbols,
            key=lambda symbol: (
                symbol.line_start,
                symbol.qualified_name,
                symbol.kind,
            ),
        )
