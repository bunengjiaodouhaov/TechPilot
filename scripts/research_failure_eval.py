from __future__ import annotations

import asyncio
from pathlib import Path

from app.harness.agent_event import InMemoryAgentEventSink
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
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
            arguments={"path": "../definitely-outside-techpilot.txt"},
            reason=(
                "Exercise the real RepositoryReadBoundary with a path escape attempt."
            ),
        )


async def main() -> None:
    root = Path.cwd()
    boundary = RepositoryReadBoundary(root)

    registry = ToolRegistry()
    registry.register(ReadFileTool(boundary))

    sink = InMemoryAgentEventSink()
    runtime = ToolRuntime(event_sink=sink)

    trace_id = "day31-path-escape"
    graph = build_research_graph(
        registry=registry,
        runtime=runtime,
        planner=SingleObjectivePlanner(),
        action_selector=EscapeReadSelector(),
        verifier=RepositoryEvidenceVerifier(),
        finalizer=EvidenceReportFinalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "Read a file outside the repository root.",
            "max_steps": 3,
        },
        context={"trace_id": trace_id},
    )

    events = sink.events_for_trace(trace_id)
    last_tool_result = result.get("last_tool_result")

    print("case_id: repository-path-escape")
    print("task_success: False")
    print("termination_reason:", result["termination_reason"].value)
    print(
        "termination_correctness:",
        result["termination_reason"] is TerminationReason.PERMANENT_FAILURE,
    )
    print("step_count:", result["step_count"])
    print(
        "error_code:",
        (
            last_tool_result.error_code.value
            if last_tool_result is not None
            and last_tool_result.error_code is not None
            else None
        ),
    )
    print(
        "trace:",
        [
            (
                event.event_type.value,
                event.tool_name,
                event.error_code,
            )
            for event in events
        ],
    )

    assert result["termination_reason"] is TerminationReason.PERMANENT_FAILURE
    assert result["step_count"] == 1
    assert last_tool_result is not None
    assert last_tool_result.error_code is not None
    assert last_tool_result.error_code.value == "execution_error"


if __name__ == "__main__":
    asyncio.run(main())
