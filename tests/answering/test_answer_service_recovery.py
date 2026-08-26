from typing import Any

import pytest

from app.answering.answer_service import REFUSAL_TEXT, AnswerService
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


class SequencedRetrievalService:
    def __init__(
        self,
        *,
        initial_hits: list[VectorSearchHit],
        recovery_anchors: list[VectorSearchHit],
    ) -> None:
        self.initial_hits = initial_hits
        self.recovery_anchors = recovery_anchors
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
        if len(self.calls) == 1:
            return self.initial_hits
        return self.recovery_anchors


class RecoveryChunkRepository:
    def __init__(
        self,
        *,
        initial_chunks: dict[int, StoredChunk],
        siblings: list[StoredChunk],
    ) -> None:
        self.initial_chunks = initial_chunks
        self.siblings = siblings
        self.id_calls: list[dict[str, Any]] = []
        self.parent_calls: list[dict[str, Any]] = []

    async def get_by_ids(
        self,
        *,
        chunk_ids: list[int],
        workspace_id: int,
    ) -> dict[int, StoredChunk]:
        self.id_calls.append(
            {"chunk_ids": chunk_ids, "workspace_id": workspace_id}
        )
        return {
            chunk_id: self.initial_chunks[chunk_id]
            for chunk_id in chunk_ids
            if chunk_id in self.initial_chunks
        }

    async def get_by_parent_sections(
        self,
        *,
        parent_sections: list[tuple[int, str]],
        workspace_id: int,
    ) -> list[StoredChunk]:
        self.parent_calls.append(
            {
                "parent_sections": parent_sections,
                "workspace_id": workspace_id,
            }
        )
        return self.siblings


class SequencedVerifier:
    def __init__(self, *, results: list[EvidenceVerificationResult]) -> None:
        self.results = list(results)
        self.calls: list[EvidenceVerificationInput] = []

    async def verify(
        self,
        *,
        request: EvidenceVerificationInput,
    ) -> EvidenceVerificationResult:
        self.calls.append(request)
        if not self.results:
            raise AssertionError("unexpected verifier call")
        return self.results.pop(0)


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


def hit(
    *,
    point_id: int,
    chunk_index: int,
    section: str,
    score: float = 0.9,
) -> VectorSearchHit:
    return VectorSearchHit(
        point_id=point_id,
        score=score,
        payload=ChunkVectorPayload(
            workspace_id=1,
            document_id=10,
            chunk_id=f"chunk-{point_id}",
            chunk_index=chunk_index,
            section=section,
            document_name="enterprise.docx",
            source_type="docx",
            page_start=None,
            page_end=None,
        ),
    )


def stored(
    *,
    chunk_db_id: int,
    chunk_index: int,
    section: str,
    text: str,
) -> StoredChunk:
    return StoredChunk(
        chunk_db_id=chunk_db_id,
        chunk_id=f"chunk-{chunk_db_id}",
        document_id=10,
        document_name="enterprise.docx",
        source_type="docx",
        chunk_index=chunk_index,
        section=section,
        page_start=None,
        page_end=None,
        text=text,
    )


def verification(
    state: EvidenceState,
    *,
    supporting: tuple[str, ...] = (),
    conflicting: tuple[str, ...] = (),
) -> EvidenceVerificationResult:
    reasons: tuple[EvidenceReason, ...]
    if state is EvidenceState.INSUFFICIENT:
        reasons = (EvidenceReason.RELATION_MISSING,)
    elif state is EvidenceState.CONFLICTING:
        reasons = (EvidenceReason.CONFLICTING_EVIDENCE,)
    else:
        reasons = ()
    return EvidenceVerificationResult(
        state=state,
        reasons=reasons,
        supporting_source_ids=supporting,
        conflicting_source_ids=conflicting,
        explanation="test decision",
    )


def build_service(
    *,
    verifier_results: list[EvidenceVerificationResult],
) -> tuple[
    AnswerService,
    SequencedRetrievalService,
    RecoveryChunkRepository,
    SequencedVerifier,
    FakeLLMProvider,
]:
    parent = "五、MCP Gateway v2：第三核心模块"
    initial_hit = hit(
        point_id=101,
        chunk_index=36,
        section=parent + " > Q35. Gateway 是什么？",
    )
    recovery_anchors = [
        initial_hit,
        hit(
            point_id=102,
            chunk_index=37,
            section=parent + " > Q36. Gateway 如何执行工具？",
            score=0.8,
        ),
    ]
    retrieval = SequencedRetrievalService(
        initial_hits=[initial_hit],
        recovery_anchors=recovery_anchors,
    )
    repository = RecoveryChunkRepository(
        initial_chunks={
            101: stored(
                chunk_db_id=101,
                chunk_index=36,
                section=initial_hit.payload.section or "",
                text="MCP Gateway provides the tool execution boundary.",
            )
        },
        siblings=[
            stored(
                chunk_db_id=201,
                chunk_index=39,
                section=parent + " > Q38. 为什么工具要分 READ、WRITE、DANGEROUS？",
                text="Tools are classified as READ, WRITE, or DANGEROUS before execution.",
            ),
            stored(
                chunk_db_id=202,
                chunk_index=41,
                section=parent + " > Q40. Tool Schema 如何校验？",
                text="Tool arguments are validated against the schema before the server call.",
            ),
            stored(
                chunk_db_id=203,
                chunk_index=45,
                section=parent + " > Q44. 为什么需要 HITL？",
                text="WRITE and DANGEROUS operations require approval at the runtime boundary.",
            ),
        ],
    )
    verifier = SequencedVerifier(results=verifier_results)
    llm = FakeLLMProvider(
        result=LLMAnswer(
            text="权限控制在工具执行边界完成。",
            cited_source_ids=("SOURCE_2", "SOURCE_3", "SOURCE_4"),
            refused=False,
        )
    )
    service = AnswerService(
        retrieval_service=retrieval,  # type: ignore[arg-type]
        chunk_repository=repository,  # type: ignore[arg-type]
        context_enricher=ContextEnricher(),
        context_builder=ContextBuilder(max_characters=10_000),
        evidence_verifier=verifier,
        llm_provider=llm,
        recovery_enabled=True,
        recovery_anchor_limit=20,
        recovery_parent_group_limit=2,
        recovery_max_additions=12,
    )
    return service, retrieval, repository, verifier, llm


@pytest.mark.asyncio
async def test_insufficient_first_pass_can_recover_and_generate() -> None:
    service, retrieval, repository, verifier, llm = build_service(
        verifier_results=[
            verification(EvidenceState.INSUFFICIENT),
            verification(
                EvidenceState.SUFFICIENT,
                supporting=("SOURCE_2", "SOURCE_3", "SOURCE_4"),
            ),
        ]
    )

    result = await service.answer(
        question="EnterpriseOps项目中权限校验是如何实现的？",
        workspace_id=1,
        retrieval_limit=1,
    )

    assert result.refused is False
    assert len(result.citations) == 3
    assert [call["limit"] for call in retrieval.calls] == [1, 20]
    assert len(repository.parent_calls) == 1
    assert repository.parent_calls[0]["parent_sections"] == [
        (10, "五、MCP Gateway v2：第三核心模块")
    ]
    assert len(verifier.calls) == 2
    assert len(verifier.calls[0].evidence) == 1
    assert len(verifier.calls[1].evidence) == 4
    assert "READ, WRITE, or DANGEROUS" in verifier.calls[1].evidence[1].text
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_recovery_still_refuses_when_second_verification_is_insufficient() -> None:
    service, retrieval, repository, verifier, llm = build_service(
        verifier_results=[
            verification(EvidenceState.INSUFFICIENT),
            verification(EvidenceState.INSUFFICIENT),
        ]
    )

    result = await service.answer(question="Question", workspace_id=1)

    assert result.refused is True
    assert result.text == REFUSAL_TEXT
    assert len(retrieval.calls) == 2
    assert len(repository.parent_calls) == 1
    assert len(verifier.calls) == 2
    assert llm.calls == []


@pytest.mark.asyncio
async def test_conflicting_evidence_never_triggers_recovery() -> None:
    service, retrieval, repository, verifier, llm = build_service(
        verifier_results=[
            verification(
                EvidenceState.CONFLICTING,
                conflicting=("SOURCE_1",),
            )
        ]
    )

    result = await service.answer(question="Question", workspace_id=1)

    assert result.refused is True
    assert result.text == REFUSAL_TEXT
    assert len(retrieval.calls) == 1
    assert repository.parent_calls == []
    assert len(verifier.calls) == 1
    assert llm.calls == []


def test_recovery_prioritizes_parent_sections_not_in_first_pass() -> None:
    overview = "一、项目总览"
    gateway = "五、MCP Gateway v2"
    anchors = [
        hit(
            point_id=1,
            chunk_index=2,
            section=overview + " > Q1",
        ),
        hit(
            point_id=2,
            chunk_index=4,
            section=overview + " > Q3",
        ),
        hit(
            point_id=3,
            chunk_index=36,
            section=gateway + " > Q35",
        ),
    ]

    groups = AnswerService._group_parent_sections(
        anchors,
        existing_parent_sections={(10, overview)},
    )

    assert groups[0][0] == (10, gateway)
    assert groups[1][0] == (10, overview)


def test_recovery_chunk_ranking_excludes_existing_anchor_chunks() -> None:
    gateway = "五、MCP Gateway v2"
    selected_groups = [
        (
            (10, gateway),
            ((6, 36), (16, 37), (20, 44)),
        )
    ]
    chunks = [
        stored(
            chunk_db_id=102,
            chunk_index=37,
            section=gateway + " > Q36",
            text="already retrieved anchor",
        ),
        stored(
            chunk_db_id=201,
            chunk_index=39,
            section=gateway + " > Q38",
            text="permission classification",
        ),
        stored(
            chunk_db_id=202,
            chunk_index=41,
            section=gateway + " > Q40",
            text="schema validation",
        ),
        stored(
            chunk_db_id=203,
            chunk_index=45,
            section=gateway + " > Q44",
            text="approval HITL",
        ),
    ]

    ranked = AnswerService._rank_recovery_chunks(
        chunks=chunks,
        selected_groups=selected_groups,
        exclude_chunk_ids={102},
    )

    assert [chunk.chunk_db_id for chunk in ranked] == [203, 201, 202]
