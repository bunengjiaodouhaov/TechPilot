from __future__ import annotations

import json

import pytest

from app.research.contracts import VerificationResult
from app.research.unified_agent import UnifiedResearchReasoner


class CapturingProvider:
    def __init__(self) -> None:
        self.user_prompt: str | None = None

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        self.user_prompt = user_prompt
        return {
            "kind": "no_actionable_path",
            "reason": "capture prompt",
            "unresolved_questions": ["still unresolved"],
            "action": None,
        }


@pytest.mark.asyncio
async def test_previous_unresolved_questions_are_visible_to_next_decision() -> None:
    provider = CapturingProvider()
    reasoner = UnifiedResearchReasoner(
        provider=provider,
        capabilities={"repo_explore": "read-only repository research"},
    )

    await reasoner.decide(
        {
            "query": "Explain ToolRuntime, RepoExplorer, and ToolRegistry.",
            "normalized_task": (
                "Explain ToolRuntime, RepoExplorer, and ToolRegistry."
            ),
            "verification": VerificationResult(
                sufficient=False,
                reason="ToolRegistry is still missing.",
                unresolved_questions=[
                    "How does ToolRegistry represent an unavailable tool?"
                ],
            ),
            "step_count": 2,
            "max_steps": 5,
        }
    )

    assert provider.user_prompt is not None
    marker = "Current agent state JSON:\n"
    payload_text = provider.user_prompt.split(
        marker,
        1,
    )[1].rsplit("\n\nReturn one decision JSON.", 1)[0]
    payload = json.loads(payload_text)

    assert payload["previous_verification"] == {
        "sufficient": False,
        "reason": "ToolRegistry is still missing.",
        "unresolved_questions": [
            "How does ToolRegistry represent an unavailable tool?"
        ],
    }
