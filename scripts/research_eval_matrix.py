from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.harness.agent_event import InMemoryAgentEventSink
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import (
    ToolErrorCode,
    ToolRiskLevel,
    ToolRuntime,
)
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.repo_explorer import RepoExplorer
from app.repository.tools import ReadFileTool, SearchSymbolTool
from app.research.contracts import (
    ResearchAction,
    ResearchState,
    TerminationReason,
    VerificationResult,
)
from app.research.evaluation import (
    ResearchGoldenCase,
    evaluate_research_run,
)
from app.research.execution import RepoExplorerActionExecutor
from app.research.graph import build_research_graph
from app.research.repo_workload import (
    EvidenceReportFinalizer,
    RepositoryEvidenceVerifier,
    RepositoryMechanismSelector,
    SingleObjectivePlanner,
)


class ProbeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str


class ProbeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


class TimeoutTool:
    name = "matrix_timeout"
    description = "Deterministic timeout injection."
    input_schema = ProbeInput
    output_schema = ProbeOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.001
    max_retries = 0

    async def execute(self, tool_input: ProbeInput) -> ProbeOutput:
        await asyncio.sleep(0.05)
        return ProbeOutput(value="unreachable")


class CandidateTool:
    name = "matrix_candidate"
    description = "Return a candidate that never becomes evidence."
    input_schema = ProbeInput
    output_schema = ProbeOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 1.0
    max_retries = 0

    async def execute(self, tool_input: ProbeInput) -> ProbeOutput:
        return ProbeOutput(value="candidate-only")


class StaticActionSelector:
    def __init__(self, action: ResearchAction) -> None:
        self._action = action

    def select_action(self, state: ResearchState) -> ResearchAction | None:
        return self._action


class AlwaysInsufficientVerifier:
    def verify(self, state: ResearchState) -> VerificationResult:
        return VerificationResult(
            sufficient=False,
            reason="Authoritative evidence remains insufficient.",
            unresolved_questions=["Authoritative evidence is still missing."],
        )


def _error_code(state: ResearchState) -> str | None:
    result = state.get("last_tool_result")
    if result is None or result.error_code is None:
        return None
    return result.error_code.value


async def _run_repo_case(
    *,
    raw: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    case = ResearchGoldenCase.model_validate(
        {
            key: value
            for key, value in raw.items()
            if key
            in {
                "case_id",
                "query",
                "repo_query",
                "search_mode",
                "expected_action",
                "expected_termination",
                "max_steps",
                "evidence_requirements",
            }
        }
    )

    boundary = RepositoryReadBoundary(root)
    registry = ToolRegistry()
    registry.register(SearchSymbolTool(boundary))
    registry.register(ReadFileTool(boundary))

    sink = InMemoryAgentEventSink()
    runtime = ToolRuntime(event_sink=sink)
    explorer = RepoExplorer(
        repository=root.name,
        registry=registry,
        runtime=runtime,
        event_sink=sink,
    )

    trace_id = f"day31-matrix-{case.case_id}"
    graph = build_research_graph(
        executor=RepoExplorerActionExecutor(explorer=explorer),
        planner=SingleObjectivePlanner(),
        action_selector=RepositoryMechanismSelector(
            repo_query=case.repo_query,
            search_mode=case.search_mode,
            limit=5,
        ),
        verifier=RepositoryEvidenceVerifier(),
        finalizer=EvidenceReportFinalizer(),
    )

    state = await graph.ainvoke(
        {
            "query": case.query,
            "max_steps": case.max_steps,
        },
        context={"trace_id": trace_id},
    )
    events = sink.events_for_trace(trace_id)
    evaluated = evaluate_research_run(
        case=case,
        state=state,
        events=events,
    )

    if case.expected_termination is TerminationReason.COMPLETED:
        scenario_pass = evaluated.task_success
    else:
        scenario_pass = bool(
            not evaluated.task_success
            and evaluated.termination_correctness
            and evaluated.tool_selection_correctness
            and evaluated.evidence_coverage == 0.0
        )

    return {
        "case_id": case.case_id,
        "category": raw["category"],
        "scenario_pass": scenario_pass,
        "task_success": evaluated.task_success,
        "evidence_coverage": evaluated.evidence_coverage,
        "provenance_integrity": evaluated.provenance_integrity,
        "tool_selection_correctness": evaluated.tool_selection_correctness,
        "termination_correctness": evaluated.termination_correctness,
        "termination_reason": state["termination_reason"].value,
        "step_count": evaluated.step_count,
        "error_code": _error_code(state),
        "covered_requirement_ids": evaluated.covered_requirement_ids,
        "missing_requirement_ids": evaluated.missing_requirement_ids,
    }


async def _run_primitive_failure(
    *,
    raw: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    kind = raw["failure_kind"]
    registry = ToolRegistry()
    runtime = ToolRuntime()
    max_retries = int(raw.get("max_retries", 1))

    if kind == "root_escape":
        boundary = RepositoryReadBoundary(root)
        registry.register(ReadFileTool(boundary))
        action = ResearchAction(
            tool_name="read_file",
            arguments={"path": "../"},
            reason="Exercise the repository-root escape boundary.",
        )
    elif kind == "invalid_input":
        boundary = RepositoryReadBoundary(root)
        registry.register(ReadFileTool(boundary))
        action = ResearchAction(
            tool_name="read_file",
            arguments={},
            reason="Exercise ToolRuntime input-schema validation.",
        )
    elif kind == "timeout":
        registry.register(TimeoutTool())
        action = ResearchAction(
            tool_name="matrix_timeout",
            arguments={"query": "timeout"},
            reason="Exercise bounded retry for a retryable timeout.",
        )
    elif kind == "max_steps":
        registry.register(CandidateTool())
        action = ResearchAction(
            tool_name="matrix_candidate",
            arguments={"query": "candidate"},
            reason="Keep returning a non-authoritative candidate.",
        )
    else:
        raise ValueError(f"unknown failure_kind: {kind}")

    graph = build_research_graph(
        registry=registry,
        runtime=runtime,
        planner=SingleObjectivePlanner(),
        action_selector=StaticActionSelector(action),
        verifier=AlwaysInsufficientVerifier(),
        finalizer=EvidenceReportFinalizer(),
    )

    state = await graph.ainvoke(
        {
            "query": f"Day31 matrix case: {raw['case_id']}",
            "max_steps": int(raw["max_steps"]),
            "max_retries": max_retries,
        }
    )

    actual_termination = state["termination_reason"].value
    actual_error = _error_code(state)
    expected_termination = raw["expected_termination"]
    expected_error = raw.get("expected_error_code")

    termination_correctness = actual_termination == expected_termination
    error_correctness = (
        True if expected_error is None else actual_error == expected_error
    )

    expected_steps = {
        "root_escape": 1,
        "invalid_input": 1,
        "timeout": max_retries + 1,
        "max_steps": int(raw["max_steps"]),
    }[kind]
    step_correctness = state["step_count"] == expected_steps

    scenario_pass = bool(
        termination_correctness
        and error_correctness
        and step_correctness
    )

    return {
        "case_id": raw["case_id"],
        "category": raw["category"],
        "scenario_pass": scenario_pass,
        "task_success": False,
        "evidence_coverage": 0.0,
        "provenance_integrity": None,
        "tool_selection_correctness": True,
        "termination_correctness": termination_correctness,
        "termination_reason": actual_termination,
        "step_count": state["step_count"],
        "error_code": actual_error,
        "expected_error_code": expected_error,
        "retry_count": state.get("retry_count", 0),
        "note": (
            "Repository boundary violations currently surface through "
            "ToolRuntime as execution_error."
            if kind == "root_escape"
            else None
        ),
    }


async def main() -> None:
    root = Path.cwd()
    cases = json.loads(
        (root / "evals/research/eval_matrix.json").read_text(
            encoding="utf-8"
        )
    )

    rows: list[dict[str, Any]] = []
    for raw in cases:
        if raw["scenario"] == "repo_research":
            row = await _run_repo_case(raw=raw, root=root)
        elif raw["scenario"] == "primitive_failure":
            row = await _run_primitive_failure(raw=raw, root=root)
        else:
            raise ValueError(f"unknown scenario: {raw['scenario']}")
        rows.append(row)

    passed = sum(bool(row["scenario_pass"]) for row in rows)

    print(
        json.dumps(
            {
                "matrix_pass": f"{passed}/{len(rows)}",
                "all_passed": passed == len(rows),
                "cases": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    assert passed == len(rows), (
        f"Day31 evaluation matrix failed: {passed}/{len(rows)} passed"
    )


if __name__ == "__main__":
    asyncio.run(main())
