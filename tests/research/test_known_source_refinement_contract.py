from app.research.unified_agent import UNIFIED_REASONER_SYSTEM_PROMPT


def test_reasoner_contract_prefers_exact_path_after_source_discovery() -> None:
    prompt = UNIFIED_REASONER_SYSTEM_PROMPT

    assert "authoritative evidence already identifies an exact repository file" in prompt
    assert "materializing that exact known path" in prompt
    assert "before issuing another broad/semantic retrieval" in prompt
    assert "Known source refinement should reduce retrieval uncertainty" in prompt
