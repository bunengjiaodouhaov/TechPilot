from app.research.unified_agent import (
    UNIFIED_REASONER_SYSTEM_PROMPT,
)


def test_system_prompt_requires_cross_source_conflict_comparison() -> None:
    prompt = UNIFIED_REASONER_SYSTEM_PROMPT

    assert "materializing both sources is NOT sufficient" in prompt
    assert "Compare their concrete claims directly" in prompt
    assert "documentation drift" in prompt
    assert "Do not infer consistency merely because" in prompt
    assert "consistent, conflicting, or documentation drift" in prompt
