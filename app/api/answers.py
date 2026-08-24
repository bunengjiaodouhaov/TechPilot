from fastapi import APIRouter, Depends, HTTPException, status

from app.answering.answer_service import (
    AnswerService,
    WorkspaceNotFoundError,
)
from app.api.dependencies import get_answer_service, get_db_session
from app.models.conversation import Conversation
from app.models.conversation_turn import ConversationTurn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.schemas.answer import (
    AnswerRequest,
    AnswerResponse,
    CitationResponse,
)

router = APIRouter(prefix="/answers", tags=["answers"])


@router.post("", response_model=AnswerResponse)
async def answer_question(
    request: AnswerRequest,
    service: AnswerService = Depends(get_answer_service),
    session: AsyncSession = Depends(get_db_session),
) -> AnswerResponse:
    """Answer a question using evidence from one workspace."""
    try:
        conversation = None
        contextual_question = request.question

        # Backward compatibility:
        # /answers without conversation_id remains the original stateless API.
        # Product multi-conversation requests explicitly supply conversation_id.
        if request.conversation_id is not None:
            conversation = await session.get(
                Conversation,
                request.conversation_id,
            )
            if (
                conversation is None
                or conversation.workspace_id != request.workspace_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="conversation not found",
                )

            recent_turns = (
                await session.scalars(
                    select(ConversationTurn)
                    .where(
                        ConversationTurn.conversation_id
                        == conversation.id
                    )
                    .order_by(ConversationTurn.id.desc())
                    .limit(6)
                )
            ).all()
            recent_turns = list(reversed(recent_turns))

            if recent_turns:
                transcript = "\n".join(
                    f"{turn.role}: {turn.text}"
                    for turn in recent_turns
                )
                contextual_question = (
                    "Conversation context (use only to resolve references "
                    "and follow-up intent; factual claims still require "
                    "retrieved workspace evidence):\n"
                    + transcript
                    + "\n\nCurrent question: "
                    + request.question
                )

        result = await service.answer(
            workspace_id=request.workspace_id,
            question=contextual_question,
        )
    except WorkspaceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="workspace not found",
        ) from exc

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

    if conversation is not None:
        session.add_all(
            [
                ConversationTurn(
                    workspace_id=request.workspace_id,
                    conversation_id=conversation.id,
                    role="user",
                    text=request.question,
                    refused=False,
                    citations_json=[],
                ),
                ConversationTurn(
                    workspace_id=request.workspace_id,
                    conversation_id=conversation.id,
                    role="assistant",
                    text=result.text,
                    refused=result.refused,
                    citations_json=[
                        citation.model_dump()
                        for citation in citations
                    ],
                ),
            ]
        )

        if conversation.title == "新对话":
            normalized_title = " ".join(
                request.question.strip().split()
            )
            conversation.title = (
                normalized_title[:36] + "…"
                if len(normalized_title) > 36
                else normalized_title
            ) or "新对话"

        await session.commit()

    return AnswerResponse(
        question=request.question,
        answer=result.text,
        citations=citations,
        refused=result.refused,
    )
