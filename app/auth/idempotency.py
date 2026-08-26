from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency_record import IdempotencyRecord, IdempotencyState


class IdempotencyConflictError(RuntimeError):
    """Raised when a key is reused incompatibly or is already in flight."""


@dataclass(frozen=True, slots=True)
class IdempotencyDecision:
    record_id: int
    replay_status_code: int | None = None
    replay_response: dict[str, Any] | None = None

    @property
    def is_replay(self) -> bool:
        return self.replay_status_code is not None


class IdempotencyService:
    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def begin(
        self,
        *,
        user_id: int,
        workspace_id: int,
        operation: str,
        key: str,
        request_hash: str,
    ) -> IdempotencyDecision:
        normalized_key = key.strip()
        if not normalized_key or len(normalized_key) > 128:
            raise ValueError("Idempotency-Key must contain 1-128 characters")

        existing = await self._find(
            user_id=user_id,
            workspace_id=workspace_id,
            operation=operation,
            key=normalized_key,
        )
        if existing is not None:
            return await self._reuse(existing, request_hash=request_hash)

        record = IdempotencyRecord(
            user_id=user_id,
            workspace_id=workspace_id,
            operation=operation,
            key=normalized_key,
            request_hash=request_hash,
            state=IdempotencyState.PROCESSING.value,
        )
        self._session.add(record)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._find(
                user_id=user_id,
                workspace_id=workspace_id,
                operation=operation,
                key=normalized_key,
            )
            if existing is None:
                raise
            return await self._reuse(existing, request_hash=request_hash)

        await self._session.refresh(record)
        return IdempotencyDecision(record_id=record.id)

    async def stage_complete(
        self,
        *,
        record_id: int,
        status_code: int,
        response_json: dict[str, Any],
    ) -> None:
        record = await self._session.get(IdempotencyRecord, record_id)
        if record is None:
            raise RuntimeError("idempotency record disappeared before completion")
        record.state = IdempotencyState.COMPLETED.value
        record.status_code = status_code
        record.response_json = response_json

    async def complete(
        self,
        *,
        record_id: int,
        status_code: int,
        response_json: dict[str, Any],
    ) -> None:
        await self.stage_complete(
            record_id=record_id,
            status_code=status_code,
            response_json=response_json,
        )
        await self._session.commit()

    async def fail(self, *, record_id: int) -> None:
        await self._session.rollback()
        record = await self._session.get(IdempotencyRecord, record_id)
        if record is None:
            return
        record.state = IdempotencyState.FAILED.value
        record.status_code = None
        record.response_json = None
        await self._session.commit()

    async def _reuse(
        self,
        record: IdempotencyRecord,
        *,
        request_hash: str,
    ) -> IdempotencyDecision:
        if record.request_hash != request_hash:
            raise IdempotencyConflictError(
                "Idempotency-Key was already used for a different request"
            )
        if record.state == IdempotencyState.COMPLETED.value:
            if record.status_code is None or record.response_json is None:
                raise RuntimeError("completed idempotency record is incomplete")
            return IdempotencyDecision(
                record_id=record.id,
                replay_status_code=record.status_code,
                replay_response=dict(record.response_json),
            )
        if record.state == IdempotencyState.PROCESSING.value:
            raise IdempotencyConflictError(
                "request with this Idempotency-Key is already processing"
            )

        record.state = IdempotencyState.PROCESSING.value
        record.status_code = None
        record.response_json = None
        await self._session.commit()
        return IdempotencyDecision(record_id=record.id)

    async def _find(
        self,
        *,
        user_id: int,
        workspace_id: int,
        operation: str,
        key: str,
    ) -> IdempotencyRecord | None:
        return await self._session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.workspace_id == workspace_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.key == key,
            )
        )
