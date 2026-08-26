from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.auth.authorization import WorkspaceAccessError, WorkspaceAuthorizer, WorkspaceRoleError
from app.auth.dependencies import AuthPrincipal, get_current_user, get_workspace_authorizer
from app.models.conversation import Conversation
from app.models.conversation_turn import ConversationTurn

router = APIRouter(tags=["conversation-history"])


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="新对话", max_length=200)


class ConversationResponse(BaseModel):
    id: int
    workspace_id: int
    title: str
    turn_count: int
    created_at: datetime
    updated_at: datetime


class ConversationTurnResponse(BaseModel):
    id: int
    workspace_id: int
    conversation_id: int
    role: str
    text: str
    refused: bool
    citations: list[dict[str, Any]]
    created_at: datetime


async def _get_conversation(session: AsyncSession, conversation_id: int) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conversation


async def _require_workspace(
    *,
    authorizer: WorkspaceAuthorizer,
    principal: AuthPrincipal,
    workspace_id: int,
    owner_required: bool = False,
) -> None:
    try:
        await authorizer.require_access(
            user_id=principal.id,
            workspace_id=workspace_id,
            owner_required=owner_required,
        )
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=404, detail="workspace not found") from exc
    except WorkspaceRoleError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/workspaces/{workspace_id}/conversations", response_model=list[ConversationResponse])
async def list_workspace_conversations(
    workspace_id: Annotated[int, Path(gt=0)],
    session: AsyncSession = Depends(get_db_session),
    principal: AuthPrincipal = Depends(get_current_user),
    authorizer: WorkspaceAuthorizer = Depends(get_workspace_authorizer),
) -> list[ConversationResponse]:
    await _require_workspace(
        authorizer=authorizer,
        principal=principal,
        workspace_id=workspace_id,
    )
    rows = (
        await session.execute(
            select(Conversation, func.count(ConversationTurn.id).label("turn_count"))
            .outerjoin(
                ConversationTurn,
                ConversationTurn.conversation_id == Conversation.id,
            )
            .where(Conversation.workspace_id == workspace_id)
            .group_by(Conversation.id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        )
    ).all()
    return [
        ConversationResponse(
            id=c.id,
            workspace_id=c.workspace_id,
            title=c.title,
            turn_count=int(count or 0),
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c, count in rows
    ]


@router.post(
    "/workspaces/{workspace_id}/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_conversation(
    workspace_id: Annotated[int, Path(gt=0)],
    request: ConversationCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    principal: AuthPrincipal = Depends(get_current_user),
    authorizer: WorkspaceAuthorizer = Depends(get_workspace_authorizer),
) -> ConversationResponse:
    await _require_workspace(
        authorizer=authorizer,
        principal=principal,
        workspace_id=workspace_id,
    )
    conversation = Conversation(
        workspace_id=workspace_id,
        title=(request.title.strip() or "新对话")[:200],
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return ConversationResponse(
        id=conversation.id,
        workspace_id=conversation.workspace_id,
        title=conversation.title,
        turn_count=0,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get(
    "/conversations/{conversation_id}/history",
    response_model=list[ConversationTurnResponse],
)
async def list_conversation_history(
    conversation_id: Annotated[int, Path(gt=0)],
    session: AsyncSession = Depends(get_db_session),
    principal: AuthPrincipal = Depends(get_current_user),
    authorizer: WorkspaceAuthorizer = Depends(get_workspace_authorizer),
) -> list[ConversationTurnResponse]:
    conversation = await _get_conversation(session, conversation_id)
    await _require_workspace(
        authorizer=authorizer,
        principal=principal,
        workspace_id=conversation.workspace_id,
    )
    turns = (
        await session.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.conversation_id == conversation_id)
            .order_by(ConversationTurn.created_at.asc(), ConversationTurn.id.asc())
            .limit(500)
        )
    ).all()
    return [
        ConversationTurnResponse(
            id=t.id,
            workspace_id=t.workspace_id,
            conversation_id=t.conversation_id,
            role=t.role,
            text=t.text,
            refused=t.refused,
            citations=list(t.citations_json or []),
            created_at=t.created_at,
        )
        for t in turns
    ]


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: Annotated[int, Path(gt=0)],
    session: AsyncSession = Depends(get_db_session),
    principal: AuthPrincipal = Depends(get_current_user),
    authorizer: WorkspaceAuthorizer = Depends(get_workspace_authorizer),
) -> Response:
    conversation = await _get_conversation(session, conversation_id)
    await _require_workspace(
        authorizer=authorizer,
        principal=principal,
        workspace_id=conversation.workspace_id,
    )
    await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/workspaces/{workspace_id}/history", response_model=list[ConversationTurnResponse])
async def list_workspace_history(
    workspace_id: Annotated[int, Path(gt=0)],
    session: AsyncSession = Depends(get_db_session),
    principal: AuthPrincipal = Depends(get_current_user),
    authorizer: WorkspaceAuthorizer = Depends(get_workspace_authorizer),
) -> list[ConversationTurnResponse]:
    await _require_workspace(
        authorizer=authorizer,
        principal=principal,
        workspace_id=workspace_id,
    )
    turns = (
        await session.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.workspace_id == workspace_id)
            .order_by(ConversationTurn.created_at.asc(), ConversationTurn.id.asc())
            .limit(500)
        )
    ).all()
    return [
        ConversationTurnResponse(
            id=t.id,
            workspace_id=t.workspace_id,
            conversation_id=t.conversation_id,
            role=t.role,
            text=t.text,
            refused=t.refused,
            citations=list(t.citations_json or []),
            created_at=t.created_at,
        )
        for t in turns
    ]
