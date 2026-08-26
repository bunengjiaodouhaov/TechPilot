from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import delete, select

from app.answering.chunk_repository import ChunkRepository
from app.auth.idempotency import IdempotencyService
from app.db.session import AsyncSessionLocal
from app.ingestion.service import IngestionService
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.document_status import DocumentStatus
from app.models.idempotency_record import IdempotencyRecord, IdempotencyState
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.retrieval.bm25_repository import BM25ChunkRepository


class FailingIndexingService:
    async def index_document(self, *, document: Document, chunks: list[Chunk]):
        raise RuntimeError("P6 simulated post-commit indexing failure")


async def _resolve_probe_user(*, session, workspace_id: int) -> int:
    user_id = await session.scalar(
        select(WorkspaceMember.user_id)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.role == WorkspaceRole.OWNER.value,
        )
        .limit(1)
    )
    if user_id is None:
        user_id = await session.scalar(
            select(WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == workspace_id)
            .limit(1)
        )
    if user_id is None:
        raise RuntimeError(f"workspace {workspace_id} has no member for probe")
    return int(user_id)


async def _probe_idempotency(*, session, workspace_id: int, user_id: int, suffix: str) -> None:
    service = IdempotencyService(session=session)
    key = f"p6-failure-retry-{suffix}"[:128]
    request_hash = hashlib.sha256(b"p6-failure-retry-probe").hexdigest()

    first = await service.begin(
        user_id=user_id,
        workspace_id=workspace_id,
        operation="p6_failure_recovery_probe",
        key=key,
        request_hash=request_hash,
    )
    record = await session.get(IdempotencyRecord, first.record_id)
    assert record is not None
    assert record.state == IdempotencyState.PROCESSING.value

    await service.fail(record_id=first.record_id)
    record = await session.get(IdempotencyRecord, first.record_id)
    assert record is not None
    assert record.state == IdempotencyState.FAILED.value

    retry = await service.begin(
        user_id=user_id,
        workspace_id=workspace_id,
        operation="p6_failure_recovery_probe",
        key=key,
        request_hash=request_hash,
    )
    assert retry.record_id == first.record_id
    record = await session.get(IdempotencyRecord, first.record_id)
    assert record is not None
    assert record.state == IdempotencyState.PROCESSING.value

    await service.complete(
        record_id=first.record_id,
        status_code=200,
        response_json={"probe": "recovered"},
    )
    record = await session.get(IdempotencyRecord, first.record_id)
    assert record is not None
    assert record.state == IdempotencyState.COMPLETED.value
    assert record.response_json == {"probe": "recovered"}

    print("P6-5B idempotency FAILED -> retry -> COMPLETED: PASS")

    await session.execute(
        delete(IdempotencyRecord).where(IdempotencyRecord.id == first.record_id)
    )
    await session.commit()


async def _probe_ingestion_failure(*, session, workspace_id: int, suffix: str) -> None:
    filename = f"p6_failure_probe_{suffix}.md"
    service = IngestionService(
        session=session,
        indexing_service=FailingIndexingService(),  # type: ignore[arg-type]
    )

    try:
        await service.ingest(
            workspace_id=workspace_id,
            filename=filename,
            content_type="text/markdown",
            file_bytes=(
                b"# P6 Failure Recovery Probe\n\n"
                b"This committed chunk must become non-searchable when indexing fails.\n"
            ),
        )
    except RuntimeError as exc:
        assert "simulated post-commit indexing failure" in str(exc)
    else:
        raise AssertionError("simulated indexing failure did not propagate")

    document = await session.scalar(
        select(Document).where(
            Document.workspace_id == workspace_id,
            Document.name == filename,
        )
    )
    assert document is not None
    assert document.status == DocumentStatus.FAILED.value
    assert "simulated post-commit indexing failure" in (document.error_message or "")

    chunk_ids = list(
        await session.scalars(
            select(Chunk.id)
            .where(Chunk.document_id == document.id)
            .order_by(Chunk.id)
        )
    )
    assert chunk_ids, "post-commit failure probe expected persisted chunks"

    authoritative = await ChunkRepository(session=session).get_by_ids(
        chunk_ids=chunk_ids,
        workspace_id=workspace_id,
    )
    assert authoritative == {}, (
        "FAILED document chunks crossed the authoritative answer boundary"
    )

    bm25_corpus = await BM25ChunkRepository(session=session).list_searchable(
        workspace_id=workspace_id,
    )
    bm25_ids = {chunk.point_id for chunk in bm25_corpus}
    assert bm25_ids.isdisjoint(chunk_ids), (
        "FAILED document chunks crossed the BM25 searchable boundary"
    )

    print(
        "P6-5D committed chunks + indexing failure -> FAILED + non-searchable: PASS"
    )
    print(f"  probe_document_id={document.id} persisted_chunk_count={len(chunk_ids)}")

    await session.execute(delete(Document).where(Document.id == document.id))
    await session.commit()


async def run(*, workspace_id: int) -> None:
    suffix = uuid.uuid4().hex[:12]
    async with AsyncSessionLocal() as session:
        user_id = await _resolve_probe_user(
            session=session,
            workspace_id=workspace_id,
        )
        print(f"workspace_id={workspace_id} probe_user_id={user_id}")
        await _probe_idempotency(
            session=session,
            workspace_id=workspace_id,
            user_id=user_id,
            suffix=suffix,
        )
        await _probe_ingestion_failure(
            session=session,
            workspace_id=workspace_id,
            suffix=suffix,
        )

    print("P6 failure recovery live DB probe: PASS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Live P6 failure-recovery probe using real PostgreSQL. "
            "Creates and cleans temporary probe records."
        )
    )
    parser.add_argument("--workspace-id", type=int, required=True)
    args = parser.parse_args()
    if args.workspace_id <= 0:
        parser.error("--workspace-id must be positive")
    return args


def main() -> None:
    args = parse_args()
    asyncio.run(run(workspace_id=args.workspace_id))


if __name__ == "__main__":
    main()
