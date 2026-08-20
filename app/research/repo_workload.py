from __future__ import annotations

from app.research.contracts import (
    ResearchAction,
    ResearchState,
    ResearchStep,
    TerminationReason,
    VerificationResult,
)


class SingleObjectivePlanner:
    """Day31 deterministic planner for one real repository mechanism task."""

    def plan(self, normalized_task: str) -> list[ResearchStep]:
        return [
            ResearchStep(
                objective=normalized_task,
                source_requirement="authoritative repository source",
            )
        ]


class RepositoryMechanismSelector:
    """
    Select the existing RepoExplorer composite capability once.

    The search term is workload metadata for this first deterministic case;
    later an LLM-backed selector will derive it from the objective.
    """

    def __init__(
        self,
        *,
        repo_query: str,
        search_mode: str = "symbol",
        limit: int = 5,
    ) -> None:
        self._repo_query = repo_query
        self._search_mode = search_mode
        self._limit = limit

    def select_action(
        self,
        state: ResearchState,
    ) -> ResearchAction | None:
        if state.get("evidence_pack") is not None:
            return None

        plan = state.get("plan") or []
        if not plan:
            return None

        return ResearchAction(
            tool_name="repo_explore",
            arguments={
                "query": self._repo_query,
                "task_intent": plan[state.get("current_step", 0)].objective,
                "search_mode": self._search_mode,
                "limit": self._limit,
            },
            reason=(
                "The objective asks how repository code implements the mechanism; "
                "use RepoExplorer to materialize authoritative code evidence."
            ),
        )


class RepositoryEvidenceVerifier:
    """First deterministic verifier; coverage scoring comes next."""

    def verify(self, state: ResearchState) -> VerificationResult:
        pack = state.get("evidence_pack")
        if (
            pack is not None
            and pack.provenance_integrity
            and len(pack.evidence) > 0
        ):
            return VerificationResult(
                sufficient=True,
                reason=(
                    "Authoritative repository evidence exists with intact provenance."
                ),
            )

        return VerificationResult(
            sufficient=False,
            reason="No authoritative repository evidence is available yet.",
            unresolved_questions=[
                "The implementation mechanism still lacks materialized code evidence."
            ],
        )


class EvidenceReportFinalizer:
    """
    Produce a deterministic evidence report.

    This intentionally does not pretend to be final LLM synthesis yet.
    """

    def finalize(self, state: ResearchState) -> str:
        reason = state["termination_reason"]
        pack = state.get("evidence_pack")

        if (
            reason is not TerminationReason.COMPLETED
            or pack is None
            or not pack.evidence
        ):
            return f"Research incomplete: {reason.value}"

        lines = ["Research completed with authoritative repository evidence:"]
        for item in pack.evidence:
            lines.append(
                f"- {item.file_path}:{item.line_start}-{item.line_end}"
                + (f" ({item.symbol})" if item.symbol else "")
            )
        return "\n".join(lines)

class DecisionReportFinalizer:
    """Deliver the semantic decision as a user-facing research result.

    COMPLETE uses the reasoner's evidence-grounded conclusion. Incomplete
    termination preserves the control reason and unresolved questions. Sources
    are appended from authoritative EvidencePack paths only.
    """

    def finalize(self, state: ResearchState) -> str:
        termination = state["termination_reason"]
        decision = state.get("decision")
        verification = state.get("verification")
        pack = state.get("evidence_pack")

        source_paths: list[str] = []
        if pack is not None:
            source_paths = list(
                dict.fromkeys(item.file_path for item in pack.evidence)
            )

        if termination is TerminationReason.COMPLETED:
            conclusion = getattr(decision, "reason", None)
            if not conclusion:
                conclusion = "Research completed."
            lines = [str(conclusion).strip()]
        else:
            lines = [f"Research incomplete: {termination.value}"]

            detail = None
            if verification is not None:
                detail = verification.reason
            elif decision is not None:
                detail = getattr(decision, "reason", None)

            if detail:
                lines.append(f"Reason: {str(detail).strip()}")

            unresolved = []
            if verification is not None:
                unresolved = list(verification.unresolved_questions)
            elif decision is not None:
                unresolved = list(
                    getattr(decision, "unresolved_questions", [])
                )

            if unresolved:
                lines.append("Unresolved:")
                lines.extend(f"- {item}" for item in unresolved)

        if source_paths:
            lines.append("Sources:")
            lines.extend(f"- {path}" for path in source_paths)

        return "\n".join(lines)
