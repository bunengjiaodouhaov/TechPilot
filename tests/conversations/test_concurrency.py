from types import SimpleNamespace

import pytest

from app.conversations.concurrency import (
    ConversationWriteConflict,
    append_exchange_if_version,
)


class FakeSession:
    def __init__(self, *, rowcount: int) -> None:
        self.rowcount = rowcount
        self.rollback_count = 0
        self.added: list[object] = []

    async def execute(self, _statement: object) -> object:
        return SimpleNamespace(rowcount=self.rowcount)

    async def rollback(self) -> None:
        self.rollback_count += 1

    def add_all(self, items: list[object]) -> None:
        self.added.extend(items)


@pytest.mark.asyncio
async def test_matching_version_stages_exactly_one_exchange() -> None:
    session = FakeSession(rowcount=1)

    await append_exchange_if_version(
        session=session,  # type: ignore[arg-type]
        conversation_id=9,
        workspace_id=2,
        expected_version=4,
        title="并发状态",
        user_text="问题",
        assistant_text="回答",
        refused=False,
        citations=[{"document_name": "architecture.md"}],
    )

    assert session.rollback_count == 0
    assert len(session.added) == 2
    assert session.added[0].role == "user"
    assert session.added[1].role == "assistant"


@pytest.mark.asyncio
async def test_stale_version_fails_closed_without_appending_turns() -> None:
    session = FakeSession(rowcount=0)

    with pytest.raises(ConversationWriteConflict):
        await append_exchange_if_version(
            session=session,  # type: ignore[arg-type]
            conversation_id=9,
            workspace_id=2,
            expected_version=4,
            title="并发状态",
            user_text="问题",
            assistant_text="回答",
            refused=False,
            citations=[],
        )

    assert session.rollback_count == 1
    assert session.added == []
