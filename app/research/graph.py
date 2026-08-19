from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.research.contracts import ResearchContext, ResearchState
from app.research.execution import (
    ResearchActionExecutor,
    ToolRuntimeActionExecutor,
)
from app.research.nodes import (
    ResearchActionSelector,
    ResearchFinalizer,
    ResearchNodes,
    ResearchPlanner,
    ResearchVerifier,
    should_finalize,
)


def build_research_graph(
    *,
    planner: ResearchPlanner,
    action_selector: ResearchActionSelector,
    verifier: ResearchVerifier,
    finalizer: ResearchFinalizer,
    executor: ResearchActionExecutor | None = None,
    registry: ToolRegistry | None = None,
    runtime: ToolRuntime | None = None,
):
    if executor is None:
        if registry is None or runtime is None:
            raise ValueError(
                "executor or both registry and runtime must be provided"
            )
        executor = ToolRuntimeActionExecutor(
            registry=registry,
            runtime=runtime,
        )

    nodes = ResearchNodes(
        executor=executor,
        planner=planner,
        action_selector=action_selector,
        verifier=verifier,
        finalizer=finalizer,
    )

    builder = StateGraph(
        ResearchState,
        context_schema=ResearchContext,
    )

    builder.add_node("normalize", nodes.normalize)
    builder.add_node("plan", nodes.plan)
    builder.add_node("act", nodes.act)
    builder.add_node("verify", nodes.verify)
    builder.add_node("finalize", nodes.finalize)

    builder.add_edge(START, "normalize")
    builder.add_edge("normalize", "plan")
    builder.add_edge("plan", "act")
    builder.add_edge("act", "verify")

    def route_after_verify(
        state: ResearchState,
    ) -> Literal["act", "finalize"]:
        if should_finalize(state):
            return "finalize"
        return "act"

    builder.add_conditional_edges(
        "verify",
        route_after_verify,
        {
            "act": "act",
            "finalize": "finalize",
        },
    )
    builder.add_edge("finalize", END)

    return builder.compile()
