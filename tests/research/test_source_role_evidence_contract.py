from __future__ import annotations

from app.harness.evidence_pack import EvidencePack
from app.repository.code_evidence import CodeEvidence
from app.research.unified_agent import (
    UNIFIED_REASONER_SYSTEM_PROMPT,
    UnifiedResearchReasoner,
)


class Provider:
    async def generate_json(self, *, system_prompt: str, user_prompt: str):
        raise AssertionError("not used")


def test_reasoner_contract_distinguishes_test_from_implementation_evidence() -> None:
    assert "Tests may corroborate implementation behavior" in UNIFIED_REASONER_SYSTEM_PROMPT
    assert "must not substitute for production implementation evidence" in UNIFIED_REASONER_SYSTEM_PROMPT


def test_prompt_exposes_source_role() -> None:
    reasoner = UnifiedResearchReasoner(
        provider=Provider(),
        capabilities={"repo_explore": "repository research"},
    )
    pack = EvidencePack(
        query="trace retry control",
        task_intent="trace retry control",
        evidence=[
            CodeEvidence(
                repository="TechPilot",
                file_path="tests/research/test_control.py",
                symbol="test_retry",
                line_start=1,
                line_end=3,
                snippet="assert retry_count == 2",
            ),
            CodeEvidence(
                repository="TechPilot",
                file_path="app/research/unified_agent.py",
                symbol="UnifiedResearchNodes.decide",
                line_start=1,
                line_end=3,
                snippet="decision_retry_count += 1",
            ),
        ],
        provenance_integrity=True,
        incomplete=False,
        issues=[],
    )

    prompt = reasoner._build_user_prompt(
        {
            "query": "trace retry control",
            "evidence_pack": pack,
        }
    )

    assert '"source_role": "test"' in prompt
    assert '"source_role": "production"' in prompt
