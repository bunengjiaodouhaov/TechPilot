import pytest

from app.auth.idempotency import IdempotencyConflictError, IdempotencyService
from app.models.idempotency_record import IdempotencyRecord, IdempotencyState


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0

    async def commit(self) -> None:
        self.commit_count += 1


@pytest.mark.asyncio
async def test_completed_idempotency_record_replays_exact_response() -> None:
    service = IdempotencyService(session=FakeSession())  # type: ignore[arg-type]
    record = IdempotencyRecord(
        id=11,
        user_id=7,
        workspace_id=2,
        operation="answer_question",
        key="same-key",
        request_hash="abc",
        state=IdempotencyState.COMPLETED.value,
        status_code=200,
        response_json={"answer": "cached"},
    )

    decision = await service._reuse(record, request_hash="abc")

    assert decision.is_replay is True
    assert decision.replay_status_code == 200
    assert decision.replay_response == {"answer": "cached"}


@pytest.mark.asyncio
async def test_idempotency_key_cannot_be_reused_for_different_request() -> None:
    service = IdempotencyService(session=FakeSession())  # type: ignore[arg-type]
    record = IdempotencyRecord(
        id=11,
        user_id=7,
        workspace_id=2,
        operation="answer_question",
        key="same-key",
        request_hash="abc",
        state=IdempotencyState.COMPLETED.value,
        status_code=200,
        response_json={"answer": "cached"},
    )

    with pytest.raises(IdempotencyConflictError):
        await service._reuse(record, request_hash="different")


@pytest.mark.asyncio
async def test_processing_idempotency_record_rejects_duplicate_in_flight() -> None:
    service = IdempotencyService(session=FakeSession())  # type: ignore[arg-type]
    record = IdempotencyRecord(
        id=11,
        user_id=7,
        workspace_id=2,
        operation="answer_question",
        key="same-key",
        request_hash="abc",
        state=IdempotencyState.PROCESSING.value,
    )

    with pytest.raises(IdempotencyConflictError):
        await service._reuse(record, request_hash="abc")


@pytest.mark.asyncio
async def test_failed_idempotency_record_can_be_retried() -> None:
    session = FakeSession()
    service = IdempotencyService(session=session)  # type: ignore[arg-type]
    record = IdempotencyRecord(
        id=11,
        user_id=7,
        workspace_id=2,
        operation="answer_question",
        key="same-key",
        request_hash="abc",
        state=IdempotencyState.FAILED.value,
    )

    decision = await service._reuse(record, request_hash="abc")

    assert decision.is_replay is False
    assert record.state == IdempotencyState.PROCESSING.value
    assert session.commit_count == 1
