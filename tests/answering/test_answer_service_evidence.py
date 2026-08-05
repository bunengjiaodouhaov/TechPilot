from typing import Any

import pytest

from app.answering.answer_service import (
    REFUSAL_TEXT,
    AnswerService,
    AnsweringDataConsistencyError,
    InvalidLLMCitationError,
    UnexpectedLLMRefusalError,
)
from app.answering.context_builder import ContextBuilder
from app.answering.context_enricher import ContextEnricher
from app.answering.dto import LLMAnswer, StoredChunk
from app.answering.evidence_dto import (
    EvidenceReason,
    EvidenceState,
    EvidenceVerificationInput,
    EvidenceVerificationResult,
)
from app.retrieval.dto import ChunkVectorPayload, VectorSearchHit


class FakeRetrievalService:
    def __init__(self, *, hits: list[VectorSearchHit]) -> None:
        self.hits = hits
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        limit: int,
    ) -> list[VectorSearchHit]:
        self.calls.append(
            {"query": query, "workspace_id": workspace_id, "limit": limit}
        )
        return self.hits


class FakeChunkRepository:
    def __init__(self, *, chunks: dict[int, StoredChunk]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, Any]] = []

    async def get_by_ids(
        self,
        *,
        chunk_ids: list[int],
        workspace_id: int,
    ) -> dict[int, StoredChunk]:
        self.calls.append(
            {"chunk_ids": chunk_ids, "workspace_id": workspace_id}
        )
        return self.chunks


class FakeEvidenceVerifier:
    def __init__(self, *, result: EvidenceVerificationResult) -> None:
        self.result = result
        self.calls: list[EvidenceVerificationInput] = []

    async def verify(
        self,
        *,
        request: EvidenceVerificationInput,
    ) -> EvidenceVerificationResult:
        self.calls.append(request)
        return self.result


class FakeLLMProvider:
    def __init__(self, *, result: LLMAnswer) -> None:
        self.result = result
        self.calls: list[dict[str, str]] = []

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMAnswer:
        self.calls.append(
            {"system_prompt": system_prompt, "user_prompt": user_prompt}
        )
        return self.result


def make_hit(*, point_id: int = 101, score: float = 0.91) -> VectorSearchHit:
    return VectorSearchHit(
        point_id=point_id,
        score=score,
        payload=ChunkVectorPayload(
            workspace_id=1,
            document_id=10,
            chunk_id=f"payload-{point_id}",
            chunk_index=0,
            section="Payload section",
            document_name="payload.pdf",
            source_type="pdf",
            page_start=1,
            page_end=1,
        ),
    )


def make_stored_chunk(
    *,
    chunk_db_id: int = 101,
    text: str = "TechPilot uses multilingual-e5-base for dense retrieval.",
) -> StoredChunk:
    return StoredChunk(
        chunk_db_id=chunk_db_id,
        chunk_id=f"stored-{chunk_db_id}",
        document_id=20,
        document_name="postgresql.pdf",
        source_type="pdf",
        chunk_index=3,
        section="Retrieval",
        page_start=4,
        page_end=5,
        text=text,
    )


def verification(
    state: EvidenceState,
    *,
    supporting: tuple[str, ...] = (),
    reasons: tuple[EvidenceReason, ...] = (),
    conflicting: tuple[str, ...] = (),
) -> EvidenceVerificationResult:
    return EvidenceVerificationResult(
        state=state,
        reasons=reasons,
        supporting_source_ids=supporting,
        conflicting_source_ids=conflicting,
        explanation="test decision",
    )


def make_service(
    *,
    hits: list[VectorSearchHit],
    chunks: dict[int, StoredChunk],
    verifier_result: EvidenceVerificationResult,
    llm_answer: LLMAnswer,
    max_characters: int = 10_000,
) -> tuple[
    AnswerService,
    FakeRetrievalService,
    FakeChunkRepository,
    FakeEvidenceVerifier,
    FakeLLMProvider,
]:
    retrieval = FakeRetrievalService(hits=hits)
    repository = FakeChunkRepository(chunks=chunks)
    verifier = FakeEvidenceVerifier(result=verifier_result)
    llm = FakeLLMProvider(result=llm_answer)
    service = AnswerService(
        retrieval_service=retrieval,  # type: ignore[arg-type]
        chunk_repository=repository,  # type: ignore[arg-type]
        context_enricher=ContextEnricher(),
        context_builder=ContextBuilder(max_characters=max_characters),
        evidence_verifier=verifier,
        llm_provider=llm,
    )
    return service, retrieval, repository, verifier, llm


@pytest.mark.asyncio
async def test_answer_verifies_only_sources_in_built_context_before_generation() -> None:
    service, _, _, verifier, llm = make_service(
        hits=[make_hit()],
        chunks={101: make_stored_chunk()},
        verifier_result=verification(
            EvidenceState.SUFFICIENT,
            supporting=("SOURCE_1",),
        ),
        llm_answer=LLMAnswer(
            text="TechPilot uses multilingual-e5-base.",
            cited_source_ids=("SOURCE_1",),
            refused=False,
        ),
    )

    result = await service.answer(
        question=" Which embedding model does TechPilot use? ",
        workspace_id=1,
    )

    assert result.refused is False
    assert len(result.citations) == 1
    assert len(verifier.calls) == 1
    request = verifier.calls[0]
    assert request.target == "Which embedding model does TechPilot use?"
    assert len(request.evidence) == 1
    assert request.evidence[0].source_id == "SOURCE_1"
    assert request.evidence[0].text == (
        "TechPilot uses multilingual-e5-base for dense retrieval."
    )
    assert len(llm.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verifier_result",
    [
        verification(
            EvidenceState.INSUFFICIENT,
            reasons=(EvidenceReason.RELATION_MISSING,),
        ),
        verification(
            EvidenceState.CONFLICTING,
            reasons=(EvidenceReason.CONFLICTING_EVIDENCE,),
            conflicting=("SOURCE_1",),
        ),
    ],
)
async def test_answer_refuses_from_evidence_state_without_generation(
    verifier_result: EvidenceVerificationResult,
) -> None:
    service, _, _, verifier, llm = make_service(
        hits=[make_hit()],
        chunks={101: make_stored_chunk()},
        verifier_result=verifier_result,
        llm_answer=LLMAnswer(
            text="Must not be used",
            cited_source_ids=("SOURCE_1",),
            refused=False,
        ),
    )

    result = await service.answer(question="Question", workspace_id=1)

    assert result.text == REFUSAL_TEXT
    assert result.refused is True
    assert result.citations == ()
    assert len(verifier.calls) == 1
    assert llm.calls == []


@pytest.mark.asyncio
async def test_answer_rejects_llm_refusal_after_sufficient_evidence() -> None:
    service, _, _, _, _ = make_service(
        hits=[make_hit()],
        chunks={101: make_stored_chunk()},
        verifier_result=verification(
            EvidenceState.SUFFICIENT,
            supporting=("SOURCE_1",),
        ),
        llm_answer=LLMAnswer(
            text="Insufficient evidence",
            cited_source_ids=(),
            refused=True,
        ),
    )

    with pytest.raises(
        UnexpectedLLMRefusalError,
        match="after evidence verifier returned sufficient",
    ):
        await service.answer(question="Question", workspace_id=1)


@pytest.mark.asyncio
async def test_answer_rejects_citation_not_verified_as_supporting() -> None:
    service, _, _, _, _ = make_service(
        hits=[make_hit(point_id=101), make_hit(point_id=102)],
        chunks={
            101: make_stored_chunk(chunk_db_id=101),
            102: make_stored_chunk(
                chunk_db_id=102,
                text="Related but not supporting evidence.",
            ),
        },
        verifier_result=verification(
            EvidenceState.SUFFICIENT,
            supporting=("SOURCE_1",),
        ),
        llm_answer=LLMAnswer(
            text="Answer",
            cited_source_ids=("SOURCE_2",),
            refused=False,
        ),
    )

    with pytest.raises(
        InvalidLLMCitationError,
        match="not verified as supporting: SOURCE_2",
    ):
        await service.answer(question="Question", workspace_id=1)


@pytest.mark.asyncio
async def test_answer_rejects_non_refused_answer_without_citation() -> None:
    service, _, _, _, _ = make_service(
        hits=[make_hit()],
        chunks={101: make_stored_chunk()},
        verifier_result=verification(
            EvidenceState.SUFFICIENT,
            supporting=("SOURCE_1",),
        ),
        llm_answer=LLMAnswer(
            text="Answer",
            cited_source_ids=(),
            refused=False,
        ),
    )

    with pytest.raises(
        InvalidLLMCitationError,
        match="without citing verified evidence",
    ):
        await service.answer(question="Question", workspace_id=1)


@pytest.mark.asyncio
async def test_answer_does_not_verify_when_retrieval_returns_no_hits() -> None:
    service, _, repository, verifier, llm = make_service(
        hits=[],
        chunks={},
        verifier_result=verification(EvidenceState.SUFFICIENT, supporting=("SOURCE_1",)),
        llm_answer=LLMAnswer(text="Unused", cited_source_ids=("SOURCE_1",), refused=False),
    )

    result = await service.answer(question="Unknown", workspace_id=1)

    assert result.refused is True
    assert repository.calls == []
    assert verifier.calls == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_answer_raises_when_all_hits_are_missing_from_postgres() -> None:
    service, _, _, verifier, llm = make_service(
        hits=[make_hit()],
        chunks={},
        verifier_result=verification(EvidenceState.INSUFFICIENT, reasons=(EvidenceReason.NO_EVIDENCE,)),
        llm_answer=LLMAnswer(text="Unused", cited_source_ids=(), refused=True),
    )

    with pytest.raises(
        AnsweringDataConsistencyError,
        match="all retrieved chunks are missing from PostgreSQL",
    ):
        await service.answer(question="Question", workspace_id=1)

    assert verifier.calls == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_answer_verifier_never_receives_context_budget_omitted_source() -> None:
    first = make_stored_chunk(
        chunk_db_id=101,
        text="First authoritative evidence.",
    )
    second = make_stored_chunk(
        chunk_db_id=102,
        text="Second evidence that must be omitted by the context budget.",
    )

    # 170 characters fits the first formatted source block but not both.
    service, _, _, verifier, _ = make_service(
        hits=[make_hit(point_id=101), make_hit(point_id=102)],
        chunks={101: first, 102: second},
        verifier_result=verification(
            EvidenceState.SUFFICIENT,
            supporting=("SOURCE_1",),
        ),
        llm_answer=LLMAnswer(
            text="Answer",
            cited_source_ids=("SOURCE_1",),
            refused=False,
        ),
        max_characters=170,
    )

    await service.answer(question="Question", workspace_id=1)

    assert len(verifier.calls) == 1
    request = verifier.calls[0]
    assert [item.source_id for item in request.evidence] == ["SOURCE_1"]
    assert [item.source_ref for item in request.evidence] == ["stored-101"]
    assert all("Second evidence" not in item.text for item in request.evidence)


@pytest.mark.asyncio
async def test_answer_generation_sees_only_verifier_supporting_sources() -> None:
    service, _, _, _, llm = make_service(
        hits=[make_hit(point_id=101), make_hit(point_id=102)],
        chunks={
            101: make_stored_chunk(
                chunk_db_id=101,
                text="Verified supporting evidence.",
            ),
            102: make_stored_chunk(
                chunk_db_id=102,
                text="Relevant but unverified evidence that must not reach generation.",
            ),
        },
        verifier_result=verification(
            EvidenceState.SUFFICIENT,
            supporting=("SOURCE_1",),
        ),
        llm_answer=LLMAnswer(
            text="Answer",
            cited_source_ids=("SOURCE_1",),
            refused=False,
        ),
    )

    await service.answer(question="Question", workspace_id=1)

    assert len(llm.calls) == 1
    user_prompt = llm.calls[0]["user_prompt"]
    assert "[SOURCE_1]" in user_prompt
    assert "Verified supporting evidence." in user_prompt
    assert "[SOURCE_2]" not in user_prompt
    assert "unverified evidence" not in user_prompt
