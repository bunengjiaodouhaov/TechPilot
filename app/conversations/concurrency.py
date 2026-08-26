from __future__ import annotations

from typing import Any

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_turn import ConversationTurn


class ConversationWriteConflict(RuntimeError):
    """Raised when an exchange was generated from stale conversation state."""


async def append_exchange_if_version(
    *,
    session: AsyncSession,
    conversation_id: int,
    workspace_id: int,
    expected_version: int,
    title: str,
    user_text: str,
    assistant_text: str,
    refused: bool,
    citations: list[dict[str, Any]],
) -> None:
    """Stage one exchange only when the conversation version still matches.

    The version bump and both turns share the caller's transaction. The caller
    decides when to commit so adjacent state such as an idempotency completion
    can be committed atomically with the conversation write.
    """
    result = await session.execute(
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace_id,
            Conversation.version == expected_version,
        )
        .values(
            version=Conversation.version + 1,
            title=title,
            updated_at=func.now(),
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        raise ConversationWriteConflict(
            "conversation changed while the answer was generating"
        )

    session.add_all(
        [
            ConversationTurn(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                role="user",
                text=user_text,
                refused=False,
                citations_json=[],
            ),
            ConversationTurn(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                role="assistant",
                text=assistant_text,
                refused=refused,
                citations_json=citations,
            ),
        ]
    )
