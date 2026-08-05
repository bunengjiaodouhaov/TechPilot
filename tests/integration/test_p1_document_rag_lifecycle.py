from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.answering.answer_service import AnswerService
from app.answering.chunk_repository import ChunkRepository
from app.answering.context_builder import ContextBuilder
from app.answering.context_enricher import ContextEnricher
from app.answering.dto import LLMAnswer
from app.answering.evidence_dto import (
    EvidenceState,
    EvidenceVerificationInput,
    EvidenceVerificationResult,
)
from app.answering.workspace_repository import WorkspaceRepository
from app.api.dependencies import (
    get_answer_service,
    get_db_session,
    get_dense_retrieval_service,
    get_vector_repository,
)
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.document import Document
from app.models.workspace import Workspace


class PromptCheckingFakeEvidenceVerifier:
    """Deterministic verifier boundary for the lifecycle integration test."""

    def __init__(self, *, marker: str) -> None:
        self._marker = marker
        self.calls: list[EvidenceVerificationInput] = []

    async def verify(
        self,
        *,
        request: EvidenceVerificationInput,
    ) -> EvidenceVerificationResult:
        if not request.evidence:
            raise AssertionError("P1 lifecycle verifier received no evidence")

        if request.evidence[0].source_id != "SOURCE_1":
            raise AssertionError(
                "P1 lifecycle verifier did not receive SOURCE_1"
            )

        if self._marker not in request.evidence[0].text:
            raise AssertionError(
                "P1 lifecycle verifier did not receive the uploaded evidence"
            )

        self.calls.append(request)
        return EvidenceVerificationResult(
            state=EvidenceState.SUFFICIENT,
            reasons=(),
            supporting_source_ids=("SOURCE_1",),
            conflicting_source_ids=(),
            explanation="The uploaded lifecycle evidence directly supports the target.",
        )


class PromptCheckingFakeLLM:
    """Deterministic LLM boundary that rejects a broken source prompt."""

    def __init__(self, *, marker: str) -> None:
        self._marker = marker
        self.calls: list[dict[str, str]] = []

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMAnswer:
        if "[SOURCE_1]" not in user_prompt:
            raise AssertionError(
                "P1 lifecycle prompt did not contain SOURCE_1"
            )

        if self._marker not in user_prompt:
            raise AssertionError(
                "P1 lifecycle prompt did not contain the uploaded evidence"
            )

        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )

        return LLMAnswer(
            text=f"可信链路标识是 {self._marker}。",
            cited_source_ids=("SOURCE_1",),
            refused=False,
        )


@pytest.mark.asyncio
async def test_p1_document_rag_lifecycle() -> None:
    marker = f"DAY9-P1-{uuid4().hex}"
    workspace_name = f"P1 lifecycle {uuid4().hex}"
    filename = "p1-lifecycle.md"
    question = "TechPilot P1 生命周期验证标识是什么？"
    markdown = (
        "# TechPilot\n\n"
        "## P1 Lifecycle Verification\n\n"
        f"TechPilot P1 生命周期验证标识是 {marker}。\n"
    ).encode("utf-8")

    fake_verifier = PromptCheckingFakeEvidenceVerifier(marker=marker)
    fake_llm = PromptCheckingFakeLLM(marker=marker)
    document_id: int | None = None

    async with AsyncSessionLocal() as session:
        workspace = Workspace(name=workspace_name)
        session.add(workspace)
        await session.commit()
        await session.refresh(workspace)
        workspace_id = workspace.id

    async def override_get_answer_service(
        session: AsyncSession = Depends(get_db_session),
    ) -> AnswerService:
        return AnswerService(
            retrieval_service=get_dense_retrieval_service(),
            chunk_repository=ChunkRepository(session=session),
            context_enricher=ContextEnricher(),
            context_builder=ContextBuilder(
                max_characters=settings.answer_context_max_characters,
            ),
            evidence_verifier=fake_verifier,
            llm_provider=fake_llm,
            workspace_repository=WorkspaceRepository(session=session),
        )

    previous_override: Any = app.dependency_overrides.get(
        get_answer_service
    )
    app.dependency_overrides[get_answer_service] = (
        override_get_answer_service
    )

    try:
        transport = ASGITransport(app=app)

        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            upload_response = await client.post(
                "/documents/upload",
                data={"workspace_id": str(workspace_id)},
                files={
                    "file": (
                        filename,
                        markdown,
                        "text/markdown",
                    )
                },
            )

            assert upload_response.status_code == 201
            upload_body = upload_response.json()
            document_id = int(upload_body["document_id"])
            assert upload_body["status"] == "COMPLETED"
            assert upload_body["file_type"] == "markdown"
            assert upload_body["chunk_count"] >= 1

            answer_response = await client.post(
                "/answers",
                json={
                    "workspace_id": workspace_id,
                    "question": question,
                },
            )

            assert answer_response.status_code == 200
            answer_body = answer_response.json()
            assert answer_body["refused"] is False
            assert marker in answer_body["answer"]
            assert len(answer_body["citations"]) == 1

            citation = answer_body["citations"][0]
            assert citation["document_name"] == filename
            assert citation["section"] == (
                "TechPilot > P1 Lifecycle Verification"
            )
            assert citation["page_start"] is None
            assert citation["page_end"] is None
            assert marker in citation["quote"]
            assert len(fake_verifier.calls) == 1
            assert len(fake_llm.calls) == 1

            delete_response = await client.delete(
                f"/documents/{document_id}",
                params={"workspace_id": workspace_id},
            )

            assert delete_response.status_code == 204

            answer_after_delete = await client.post(
                "/answers",
                json={
                    "workspace_id": workspace_id,
                    "question": question,
                },
            )

            assert answer_after_delete.status_code == 200
            deleted_answer_body = answer_after_delete.json()
            assert deleted_answer_body["refused"] is True
            assert deleted_answer_body["citations"] == []
            assert len(fake_verifier.calls) == 1
            assert len(fake_llm.calls) == 1

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document).where(Document.id == document_id)
            )
            document = result.scalar_one()
            assert document.deleted_at is not None

    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_answer_service, None)
        else:
            app.dependency_overrides[get_answer_service] = (
                previous_override
            )

        if document_id is not None:
            await get_vector_repository().delete_document_points(
                workspace_id=workspace_id,
                document_id=document_id,
            )

        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(Workspace).where(Workspace.id == workspace_id)
            )
            await session.commit()

        await engine.dispose()
