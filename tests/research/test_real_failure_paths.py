from __future__ import annotations

import pytest

from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolErrorCode, ToolRuntime
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.tools import ReadFileTool
from app.research.contracts import (
    ResearchAction,
    ResearchState,
    TerminationReason,
)
from app.research.graph import build_research_graph
from app.research.repo_workload import (
    EvidenceReportFinalizer,
    RepositoryEvidenceVerifier,
    SingleObjectivePlanner,
)


class EscapeReadSelector:
    def select_action(
        self,
        state: ResearchState,
    ) -> ResearchAction | None:
        if state.get("last_tool_result") is not None:
            return None

        return ResearchAction(
            tool_name="read_file",
            arguments={"path": "../outside.txt"},
            reason="Attempt to escape the repository root.",
        )


@pytest.mark.asyncio
async def test_real_read_boundary_escape_is_permanent_failure(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")

    boundary = RepositoryReadBoundary(repo)
    registry = ToolRegistry()
    registry.register(ReadFileTool(boundary))

    graph = build_research_graph(
        registry=registry,
        runtime=ToolRuntime(),
        planner=SingleObjectivePlanner(),
        action_selector=EscapeReadSelector(),
        verifier=RepositoryEvidenceVerifier(),
        finalizer=EvidenceReportFinalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "Read outside the repository.",
            "max_steps": 3,
        }
    )

    assert result["termination_reason"] is TerminationReason.PERMANENT_FAILURE
    assert result["step_count"] == 1
    assert result["last_tool_result"].error_code is ToolErrorCode.EXECUTION_ERROR
