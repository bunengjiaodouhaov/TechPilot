from __future__ import annotations

from app.harness.evidence_pack import EvidencePack
from app.repository.code_evidence import CodeEvidence
from app.research.contracts import (
    TerminationReason,
    VerificationResult,
)
from app.research.repo_workload import DecisionReportFinalizer


def pack() -> EvidencePack:
    return EvidencePack(
        query="provider timeout incident",
        task_intent="provider timeout incident",
        evidence=[
            CodeEvidence(
                repository="TechPilot",
                file_path="app/research/decision_llm.py",
                symbol="DeepSeekResearchDecisionProvider.generate_json",
                line_start=73,
                line_end=143,
                snippet="DecisionFailureCode.TIMEOUT retryable=True",
            )
        ],
        provenance_integrity=True,
        incomplete=False,
        issues=[],
    )


class Decision:
    reason = (
        "Provider timeout maps to TIMEOUT and is retryable. "
        "Decision retries are bounded and exhaust as RETRY_EXHAUSTED."
    )
    unresolved_questions = []


def test_completed_delivery_uses_semantic_conclusion_and_sources() -> None:
    result = DecisionReportFinalizer().finalize(
        {
            "query": "provider timeout incident",
            "termination_reason": TerminationReason.COMPLETED,
            "decision": Decision(),
            "evidence_pack": pack(),
        }
    )

    assert "Provider timeout maps to TIMEOUT" in result
    assert "Sources:" in result
    assert "app/research/decision_llm.py" in result


def test_incomplete_delivery_preserves_unresolved_questions() -> None:
    result = DecisionReportFinalizer().finalize(
        {
            "query": "provider timeout incident",
            "termination_reason": TerminationReason.NO_ACTIONABLE_PATH,
            "decision": None,
            "verification": VerificationResult(
                sufficient=False,
                reason="step accounting remains unresolved",
                unresolved_questions=[
                    "Does provider retry consume research steps?"
                ],
            ),
            "evidence_pack": pack(),
        }
    )

    assert "Research incomplete: no_actionable_path" in result
    assert "Does provider retry consume research steps?" in result
    assert "app/research/decision_llm.py" in result
