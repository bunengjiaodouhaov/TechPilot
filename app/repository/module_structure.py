from __future__ import annotations

import ast
from dataclasses import dataclass

from app.repository.ast_service import PythonAstParseError, PythonSymbolKind
from app.repository.read_boundary import RepositoryReadBoundary, RepositoryReadError


@dataclass(frozen=True, slots=True)
class ModuleImportRef:
    candidates: tuple[str, ...]
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class ModuleSymbolRef:
    name: str
    kind: PythonSymbolKind
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class ParsedPythonModule:
    path: str
    module_name: str
    is_package: bool
    imports: tuple[ModuleImportRef, ...]
    symbols: tuple[ModuleSymbolRef, ...]


@dataclass(frozen=True, slots=True)
class InternalModuleDependency:
    module_name: str
    path: str
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class RepositoryModule:
    path: str
    module_name: str
    internal_dependencies: tuple[InternalModuleDependency, ...]
    symbols: tuple[ModuleSymbolRef, ...]


@dataclass(frozen=True, slots=True)
class ModuleStructureReport:
    modules: tuple[RepositoryModule, ...]
    module_count: int
    python_file_count: int
    parse_error_count: int
    read_error_count: int
    truncated: bool


class PythonModuleStructureService:
    """Build deterministic Python module structure without executing code."""

    def __init__(self, *, boundary: RepositoryReadBoundary) -> None:
        self._boundary = boundary

    def inspect_repository(self, *, limit: int = 20) -> ModuleStructureReport:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        python_paths = [
            path
            for path in self._boundary.list_files()
            if path.endswith(".py")
        ]
        truncated = len(python_paths) > limit
        selected_paths = python_paths[:limit]

        parsed_modules: list[ParsedPythonModule] = []
        parse_error_count = 0
        read_error_count = 0

        for path in selected_paths:
            try:
                resolved = self._boundary.resolve_file(path)
                source = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, RepositoryReadError):
                read_error_count += 1
                continue

            try:
                parsed_modules.append(
                    self.parse_source(
                        path=path,
                        source=source,
                    )
                )
            except PythonAstParseError:
                parse_error_count += 1

        module_to_path = {
            module.module_name: module.path
            for module in parsed_modules
            if module.module_name
        }
        available_modules = set(module_to_path)

        modules = tuple(
            RepositoryModule(
                path=module.path,
                module_name=module.module_name,
                internal_dependencies=self._resolve_dependencies(
                    module=module,
                    available_modules=available_modules,
                    module_to_path=module_to_path,
                ),
                symbols=module.symbols,
            )
            for module in parsed_modules
        )

        return ModuleStructureReport(
            modules=modules,
            module_count=len(modules),
            python_file_count=len(python_paths),
            parse_error_count=parse_error_count,
            read_error_count=read_error_count,
            truncated=truncated,
        )

    @staticmethod
    def parse_source(*, path: str, source: str) -> ParsedPythonModule:
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            raise PythonAstParseError(
                f"unable to parse Python source: {path}"
            ) from exc

        module_name, is_package = _module_name_from_path(path)
        imports: list[ModuleImportRef] = []
        symbols: list[ModuleSymbolRef] = []

        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        ModuleImportRef(
                            candidates=(alias.name,),
                            line_start=node.lineno,
                            line_end=getattr(node, "end_lineno", node.lineno),
                        )
                    )
                continue

            if isinstance(node, ast.ImportFrom):
                imports.append(
                    ModuleImportRef(
                        candidates=_import_from_candidates(
                            node=node,
                            current_module=module_name,
                            is_package=is_package,
                        ),
                        line_start=node.lineno,
                        line_end=getattr(node, "end_lineno", node.lineno),
                    )
                )
                continue

            if isinstance(node, ast.ClassDef):
                symbols.append(
                    ModuleSymbolRef(
                        name=node.name,
                        kind=PythonSymbolKind.CLASS,
                        line_start=node.lineno,
                        line_end=getattr(node, "end_lineno", node.lineno),
                    )
                )
                continue

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(
                    ModuleSymbolRef(
                        name=node.name,
                        kind=PythonSymbolKind.FUNCTION,
                        line_start=node.lineno,
                        line_end=getattr(node, "end_lineno", node.lineno),
                    )
                )

        return ParsedPythonModule(
            path=path,
            module_name=module_name,
            is_package=is_package,
            imports=tuple(imports),
            symbols=tuple(symbols),
        )

    @staticmethod
    def _resolve_dependencies(
        *,
        module: ParsedPythonModule,
        available_modules: set[str],
        module_to_path: dict[str, str],
    ) -> tuple[InternalModuleDependency, ...]:
        dependencies: list[InternalModuleDependency] = []
        seen: set[tuple[str, int, int]] = set()

        for import_ref in module.imports:
            resolved_name = _resolve_available_module(
                candidates=import_ref.candidates,
                available_modules=available_modules,
            )
            if resolved_name is None or resolved_name == module.module_name:
                continue

            identity = (
                resolved_name,
                import_ref.line_start,
                import_ref.line_end,
            )
            if identity in seen:
                continue
            seen.add(identity)

            dependencies.append(
                InternalModuleDependency(
                    module_name=resolved_name,
                    path=module_to_path[resolved_name],
                    line_start=import_ref.line_start,
                    line_end=import_ref.line_end,
                )
            )

        return tuple(dependencies)


def _module_name_from_path(path: str) -> tuple[str, bool]:
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    filename = parts[-1]
    stem = filename[:-3] if filename.endswith(".py") else filename
    is_package = stem == "__init__"

    module_parts = parts[:-1] if is_package else [*parts[:-1], stem]
    module_name = ".".join(part for part in module_parts if part)
    return module_name, is_package


def _import_from_candidates(
    *,
    node: ast.ImportFrom,
    current_module: str,
    is_package: bool,
) -> tuple[str, ...]:
    base = node.module or ""

    if node.level:
        package = current_module if is_package else current_module.rpartition(".")[0]
        package_parts = [part for part in package.split(".") if part]
        ascend = max(node.level - 1, 0)
        if ascend:
            package_parts = package_parts[:-ascend] if ascend <= len(package_parts) else []
        prefix = ".".join(package_parts)
        base = ".".join(part for part in (prefix, base) if part)

    candidates: list[str] = []
    for alias in node.names:
        if alias.name == "*":
            continue
        combined = ".".join(part for part in (base, alias.name) if part)
        if combined:
            candidates.append(combined)

    if base:
        candidates.append(base)

    return tuple(dict.fromkeys(candidates))


def _resolve_available_module(
    *,
    candidates: tuple[str, ...],
    available_modules: set[str],
) -> str | None:
    for candidate in candidates:
        parts = candidate.split(".")
        for end in range(len(parts), 0, -1):
            possible = ".".join(parts[:end])
            if possible in available_modules:
                return possible
    return None
