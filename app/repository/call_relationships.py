from __future__ import annotations

from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.structure_index import (
    CallSearchReport as StaticCallReport,
    PythonRepositoryStructureIndex,
    StaticCallClue,
)


class PythonCallRelationshipService:
    """Query static call-site clues from a prebuilt repository structure snapshot."""

    def __init__(
        self,
        *,
        index: PythonRepositoryStructureIndex | None = None,
        boundary: RepositoryReadBoundary | None = None,
    ) -> None:
        if index is not None and boundary is not None:
            raise ValueError("pass index or boundary, not both")
        if index is None:
            if boundary is None:
                raise ValueError("index or boundary is required")
            index = PythonRepositoryStructureIndex(boundary=boundary)
            index.rebuild()
        self._index = index

    def inspect_repository(
        self,
        *,
        query: str,
        limit: int = 20,
    ) -> StaticCallReport:
        return self._index.search_calls(
            query=query,
            limit=limit,
        )


__all__ = [
    "PythonCallRelationshipService",
    "StaticCallClue",
    "StaticCallReport",
]
