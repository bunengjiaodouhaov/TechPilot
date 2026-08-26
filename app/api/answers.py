from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.answering.answer_service import AnswerService, WorkspaceNotFoundError
from app.api.dependencies import get_answer_service, get_db_session
from app.api.schemas.answer import AnswerRequest, AnswerResponse, CitationResponse
from app.auth.authorization import WorkspaceAccessError, WorkspaceAuthorizer, WorkspaceRoleError
from app.auth.dependencies import (
    AuthPrincipal,
    get_current_user,
    get_idempotency_service,
    get_workspace_authorizer,
)
from app.auth.idempotency import IdempotencyConflictError, IdempotencyService
from app.conversations.concurrency import (
    ConversationWriteConflict,
    append_exchange_if_version,
)
from app.models.conversation import Conversation
from app.models.conversation_turn import ConversationTurn

router = APIRouter(prefix="/answers", tags=["answers"])


def _request_hash(request: AnswerRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _workspace_access_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, WorkspaceAccessError):
        return HTTPException(status_code=404, detail="workspace not found")
    if isinstance(exc, WorkspaceRoleError):
        return HTTPException(status_code=403, detail=str(exc))
    raise exc


def _default_title(question: str) -> str:
    normalized = " ".join(question.strip().split())
    if len(normalized) > 36:
        return normalized[:36] + "…"
    return normalized or "新对话"


@router.post("", response_model=AnswerResponse)
async def answer_question(
    request: AnswerRequest,
    service: AnswerService = Depends(get_answer_service),
    session: AsyncSession = Depends(get_db_session),
    principal: AuthPrincipal = Depends(get_current_user),
    authorizer: WorkspaceAuthorizer = Depends(get_workspace_authorizer),
    idempotency: IdempotencyService = Depends(get_idempotency_service),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AnswerResponse:
    """Answer one workspace-scoped question with auth and concurrency guards."""
    idempotency_record_id: int | None = None
    conversation_id: int | None = None
    conversation_version: int | None = None
    conversation_title: str | None = None

    try:
        await authorizer.require_access(
            user_id=principal.id,
            workspace_id=request.workspace_id,
        )
    except (WorkspaceAccessError, WorkspaceRoleError) as exc:
        raise _workspace_access_http_error(exc) from exc

    if idempotency_key is not None:
        try:
            decision = await idempotency.begin(
                user_id=principal.id,
                workspace_id=request.workspace_id,
                operation="answer_question",
                key=idempotency_key,
                request_hash=_request_hash(request),
            )
        except (IdempotencyConflictError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if decision.is_replay:
            assert decision.replay_response is not None
            return AnswerResponse.model_validate(decision.replay_response)
        idempotency_record_id = decision.record_id

    try:
        contextual_question = request.question

        if request.conversation_id is not None:
            conversation = await session.get(Conversation, request.conversation_id)
            if conversation is None or conversation.workspace_id != request.workspace_id:
                raise HTTPException(status_code=404, detail="conversation not found")

            conversation_id = conversation.id
            conversation_version = conversation.version
            conversation_title = conversation.title

            recent_turns = (
                await session.scalars(
                    select(ConversationTurn)
                    .where(ConversationTurn.conversation_id == conversation.id)
                    .order_by(ConversationTurn.id.desc())
                    .limit(6)
                )
            ).all()
            recent_turns = list(reversed(recent_turns))

            if recent_turns:
                transcript = "\n".join(
                    f"{turn.role}: {turn.text}" for turn in recent_turns
                )
                contextual_question = (
                    "Conversation context (use only to resolve references "
                    "and follow-up intent; factual claims still require "
                    "retrieved workspace evidence):\n"
                    + transcript
                    + "\n\nCurrent question: "
                    + request.question
                )

            # Do not keep a database transaction open across the potentially
            # long LLM call. The captured version becomes the optimistic CAS.
            await session.commit()

        result = await service.answer(
            workspace_id=request.workspace_id,
            question=contextual_question,
        )
    except WorkspaceNotFoundError as exc:
        if idempotency_record_id is not None:
            await idempotency.fail(record_id=idempotency_record_id)
        raise HTTPException(status_code=404, detail="workspace not found") from exc
    except Exception:
        if idempotency_record_id is not None:
            await idempotency.fail(record_id=idempotency_record_id)
        raise

    citations = [
        CitationResponse(
            document_name=citation.document_name,
            page_start=citation.page_start,
            page_end=citation.page_end,
            section=citation.section,
            quote=citation.quote,
        )
        for citation in result.citations
    ]
    response = AnswerResponse(
        question=request.question,
        answer=result.text,
        citations=citations,
        refused=result.refused,
    )

    if conversation_id is not None:
        assert conversation_version is not None
        assert conversation_title is not None
        next_title = (
            _default_title(request.question)
            if conversation_title == "新对话"
            else conversation_title
        )
        try:
            await append_exchange_if_version(
                session=session,
                conversation_id=conversation_id,
                workspace_id=request.workspace_id,
                expected_version=conversation_version,
                title=next_title,
                user_text=request.question,
                assistant_text=result.text,
                refused=result.refused,
                citations=[citation.model_dump() for citation in citations],
            )
        except ConversationWriteConflict as exc:
            if idempotency_record_id is not None:
                await idempotency.fail(record_id=idempotency_record_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "conversation changed while the answer was generating; "
                    "reload the latest history and retry"
                ),
            ) from exc

        if idempotency_record_id is not None:
            await idempotency.stage_complete(
                record_id=idempotency_record_id,
                status_code=status.HTTP_200_OK,
                response_json=response.model_dump(mode="json"),
            )
        await session.commit()
    elif idempotency_record_id is not None:
        await idempotency.complete(
            record_id=idempotency_record_id,
            status_code=status.HTTP_200_OK,
            response_json=response.model_dump(mode="json"),
        )

    return response
