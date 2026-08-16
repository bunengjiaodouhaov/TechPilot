from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from app.repository.ast_service import PythonSymbolKind
from app.repository.read_boundary import RepositoryReadBoundary, RepositoryReadError


_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "between",
        "by",
        "call",
        "calls",
        "connect",
        "connects",
        "dependency",
        "dependencies",
        "does",
        "find",
        "for",
        "from",
        "how",
        "in",
        "is",
        "module",
        "modules",
        "structure",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "where",
        "which",
        "with",
    }
)


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
class StaticCallClue:
    path: str
    caller: str
    callee: str
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class StructureIndexBuildReport:
    python_file_count: int
    module_count: int
    call_clue_count: int
    parse_error_count: int
    read_error_count: int


@dataclass(frozen=True, slots=True)
class ModuleSearchReport:
    modules: tuple[RepositoryModule, ...]
    module_count: int
    python_file_count: int
    parse_error_count: int
    read_error_count: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class CallSearchReport:
    clues: tuple[StaticCallClue, ...]
    match_count: int
    python_file_count: int
    parse_error_count: int
    read_error_count: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class _StructureSnapshot:
    modules: tuple[RepositoryModule, ...]
    calls: tuple[StaticCallClue, ...]
    module_postings: dict[str, tuple[int, ...]]
    call_postings: dict[str, tuple[int, ...]]
    python_file_count: int
    parse_error_count: int
    read_error_count: int


class StructureIndexNotBuiltError(RuntimeError):
    """Raised when query-time structural retrieval is used before rebuild()."""


class PythonRepositoryStructureIndex:
    """In-memory repository structure snapshot built outside query-time retrieval."""

    def __init__(self, *, boundary: RepositoryReadBoundary) -> None:
        self._boundary = boundary
        self._snapshot: _StructureSnapshot | None = None

    def rebuild(self) -> StructureIndexBuildReport:
        python_paths = [
            path
            for path in self._boundary.list_files()
            if path.endswith(".py")
        ]

        parsed_modules: list[ParsedPythonModule] = []
        call_clues: list[StaticCallClue] = []
        parse_error_count = 0
        read_error_count = 0

        for path in python_paths:
            try:
                resolved = self._boundary.resolve_file(path)
                source = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, RepositoryReadError):
                read_error_count += 1
                continue

            try:
                tree = ast.parse(source, filename=path)
            except SyntaxError:
                parse_error_count += 1
                continue

            parsed_modules.append(_parse_module_tree(path=path, tree=tree))
            call_clues.extend(_extract_call_clues(path=path, tree=tree))

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
                internal_dependencies=_resolve_dependencies(
                    module=module,
                    available_modules=available_modules,
                    module_to_path=module_to_path,
                ),
                symbols=module.symbols,
            )
            for module in sorted(parsed_modules, key=lambda item: item.path)
        )
        calls = tuple(
            sorted(
                call_clues,
                key=lambda clue: (
                    clue.path,
                    clue.line_start,
                    clue.line_end,
                    clue.caller,
                    clue.callee,
                ),
            )
        )

        self._snapshot = _StructureSnapshot(
            modules=modules,
            calls=calls,
            module_postings=_build_module_postings(modules),
            call_postings=_build_call_postings(calls),
            python_file_count=len(python_paths),
            parse_error_count=parse_error_count,
            read_error_count=read_error_count,
        )
        return StructureIndexBuildReport(
            python_file_count=len(python_paths),
            module_count=len(modules),
            call_clue_count=len(calls),
            parse_error_count=parse_error_count,
            read_error_count=read_error_count,
        )

    def search_modules(
        self,
        *,
        query: str,
        limit: int = 20,
    ) -> ModuleSearchReport:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        snapshot = self._require_snapshot()
        query_terms = _query_terms(normalized_query)
        candidate_indexes = _candidate_indexes(
            query_terms=query_terms,
            postings=snapshot.module_postings,
        )
        if candidate_indexes:
            scored = [
                (
                    _score_module(
                        module=snapshot.modules[index],
                        query_terms=query_terms,
                    ),
                    snapshot.modules[index],
                )
                for index in candidate_indexes
            ]
            positive = [item for item in scored if item[0] > 0]
            positive.sort(
                key=lambda item: (
                    -item[0],
                    item[1].path,
                    item[1].module_name,
                )
            )
            ordered = [module for _, module in positive]
        else:
            # Generic structure questions can still inspect the prebuilt snapshot.
            # Named queries use inverted postings and never enumerate all modules.
            ordered = list(snapshot.modules) if not query_terms else []

        truncated = len(ordered) > limit
        selected = tuple(ordered[:limit])
        return ModuleSearchReport(
            modules=selected,
            module_count=len(selected),
            python_file_count=snapshot.python_file_count,
            parse_error_count=snapshot.parse_error_count,
            read_error_count=snapshot.read_error_count,
            truncated=truncated,
        )

    def search_calls(
        self,
        *,
        query: str,
        limit: int = 20,
    ) -> CallSearchReport:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        snapshot = self._require_snapshot()
        query_terms = _query_terms(normalized_query)
        candidate_indexes = _candidate_indexes(
            query_terms=query_terms,
            postings=snapshot.call_postings,
        )
        scored = [
            (
                _score_call(
                    clue=snapshot.calls[index],
                    query_terms=query_terms,
                ),
                snapshot.calls[index],
            )
            for index in candidate_indexes
        ]
        positive = [item for item in scored if item[0] > 0]
        positive.sort(
            key=lambda item: (
                -item[0],
                item[1].path,
                item[1].line_start,
                item[1].caller,
                item[1].callee,
            )
        )
        ordered = [clue for _, clue in positive]

        truncated = len(ordered) > limit
        selected = tuple(ordered[:limit])
        return CallSearchReport(
            clues=selected,
            match_count=len(selected),
            python_file_count=snapshot.python_file_count,
            parse_error_count=snapshot.parse_error_count,
            read_error_count=snapshot.read_error_count,
            truncated=truncated,
        )

    def _require_snapshot(self) -> _StructureSnapshot:
        if self._snapshot is None:
            raise StructureIndexNotBuiltError(
                "repository structure index is not built; call rebuild() first"
            )
        return self._snapshot


def _parse_module_tree(*, path: str, tree: ast.Module) -> ParsedPythonModule:
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


class _StaticCallVisitor(ast.NodeVisitor):
    def __init__(self, *, path: str) -> None:
        self._path = path
        self._scope: list[tuple[str, str]] = []
        self.clues: list[StaticCallClue] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(("class", node.name))
        for statement in node.body:
            self.visit(statement)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        self._scope.append(("function", node.name))
        for statement in node.body:
            self.visit(statement)
        self._scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        caller = self._current_caller()
        callee = _render_callee(node.func)
        if caller is not None and callee is not None:
            self.clues.append(
                StaticCallClue(
                    path=self._path,
                    caller=caller,
                    callee=callee,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                )
            )
        self.generic_visit(node)

    def _current_caller(self) -> str | None:
        if not any(kind == "function" for kind, _ in self._scope):
            return None
        return ".".join(name for _, name in self._scope)


def _extract_call_clues(*, path: str, tree: ast.Module) -> tuple[StaticCallClue, ...]:
    visitor = _StaticCallVisitor(path=path)
    visitor.visit(tree)
    return tuple(visitor.clues)


def _render_callee(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id

    if isinstance(node, ast.Attribute):
        base = _render_callee(node.value)
        if base is None and isinstance(node.value, ast.Call):
            called = _render_callee(node.value.func)
            if called is not None:
                base = f"{called}()"
        if base is None:
            return None
        return f"{base}.{node.attr}"

    return None


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
            package_parts = (
                package_parts[:-ascend]
                if ascend <= len(package_parts)
                else []
            )
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


def _query_terms(query: str) -> tuple[str, ...]:
    raw_terms: list[str] = []
    for match in _IDENTIFIER_RE.finditer(query):
        raw = match.group(0)
        raw_terms.append(raw.lower())

        for dotted_part in raw.split("."):
            for snake_part in dotted_part.split("_"):
                if not snake_part:
                    continue
                raw_terms.append(snake_part.lower())
                raw_terms.extend(
                    part.lower()
                    for part in _CAMEL_RE.findall(snake_part)
                    if part
                )

    filtered = [
        term
        for term in raw_terms
        if term and term not in _STOPWORDS
    ]
    return tuple(dict.fromkeys(filtered))


def _structure_tokens(*values: str) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        lower = value.lower()
        tokens.add(lower)
        for match in _IDENTIFIER_RE.finditer(value):
            raw = match.group(0)
            tokens.add(raw.lower())
            for dotted_part in raw.split("."):
                for snake_part in dotted_part.split("_"):
                    if not snake_part:
                        continue
                    tokens.add(snake_part.lower())
                    tokens.update(
                        part.lower()
                        for part in _CAMEL_RE.findall(snake_part)
                        if part
                    )
    return tokens


def _candidate_indexes(
    *,
    query_terms: tuple[str, ...],
    postings: dict[str, tuple[int, ...]],
) -> tuple[int, ...]:
    candidates: set[int] = set()
    for term in query_terms:
        candidates.update(postings.get(term, ()))
    return tuple(sorted(candidates))


def _build_module_postings(
    modules: tuple[RepositoryModule, ...],
) -> dict[str, tuple[int, ...]]:
    mutable: dict[str, set[int]] = {}
    for index, module in enumerate(modules):
        values = [
            module.path,
            module.module_name,
            *(symbol.name for symbol in module.symbols),
            *(
                dependency.module_name
                for dependency in module.internal_dependencies
            ),
            *(
                dependency.path
                for dependency in module.internal_dependencies
            ),
        ]
        for token in _structure_tokens(*values):
            mutable.setdefault(token, set()).add(index)
    return {
        token: tuple(sorted(indexes))
        for token, indexes in mutable.items()
    }


def _build_call_postings(
    calls: tuple[StaticCallClue, ...],
) -> dict[str, tuple[int, ...]]:
    mutable: dict[str, set[int]] = {}
    for index, clue in enumerate(calls):
        for token in _structure_tokens(clue.caller, clue.callee):
            mutable.setdefault(token, set()).add(index)
    return {
        token: tuple(sorted(indexes))
        for token, indexes in mutable.items()
    }


def _score_module(
    *,
    module: RepositoryModule,
    query_terms: tuple[str, ...],
) -> int:
    if not query_terms:
        return 0

    symbols = [symbol.name for symbol in module.symbols]
    dependencies = [
        dependency.module_name
        for dependency in module.internal_dependencies
    ]
    dependency_paths = [
        dependency.path
        for dependency in module.internal_dependencies
    ]
    values = [
        module.path,
        module.module_name,
        *symbols,
        *dependencies,
        *dependency_paths,
    ]
    tokens = _structure_tokens(*values)
    exact_names = {
        module.module_name.lower(),
        module.path.lower(),
        *(symbol.lower() for symbol in symbols),
        *(dependency.lower() for dependency in dependencies),
        *(dependency.rsplit(".", 1)[-1].lower() for dependency in dependencies),
    }

    score = 0
    haystack = " ".join(values).lower()
    for term in query_terms:
        if term in exact_names:
            score += 8
        elif term in tokens:
            score += 3
        elif term in haystack:
            score += 1
    return score


def _score_call(
    *,
    clue: StaticCallClue,
    query_terms: tuple[str, ...],
) -> int:
    if not query_terms:
        return 0

    values = [clue.caller, clue.callee]
    tokens = _structure_tokens(*values)
    exact_names = {
        clue.caller.lower(),
        clue.callee.lower(),
        clue.caller.rsplit(".", 1)[-1].lower(),
        clue.callee.rsplit(".", 1)[-1].lower(),
    }
    haystack = " ".join(values).lower()

    score = 0
    for term in query_terms:
        if term in exact_names:
            score += 8
        elif term in tokens:
            score += 3
        elif term in haystack:
            score += 1
    return score
