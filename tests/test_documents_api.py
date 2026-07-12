from fastapi.testclient import TestClient

from app.api.dependencies import get_ingestion_service
from app.ingestion.service import (
    IngestionResult,
    WorkspaceNotFoundError,
)
from app.main import app


class SuccessfulIngestionService:
    async def ingest(
        self,
        *,
        workspace_id: int,
        filename: str,
        content_type: str,
        file_bytes: bytes,
    ) -> IngestionResult:
        assert workspace_id == 1
        assert filename == "guide.md"
        assert content_type == "text/markdown"
        assert file_bytes == b"# Guide\n\nHello TechPilot.\n"

        return IngestionResult(
            document_id=42,
            status="COMPLETED",
            file_type="markdown",
            chunk_count=2,
            checksum="a" * 64,
        )


class MissingWorkspaceIngestionService:
    async def ingest(
        self,
        *,
        workspace_id: int,
        filename: str,
        content_type: str,
        file_bytes: bytes,
    ) -> IngestionResult:
        raise WorkspaceNotFoundError(
            f"Workspace {workspace_id} does not exist."
        )


def test_upload_document_returns_ingestion_result() -> None:
    app.dependency_overrides[get_ingestion_service] = (
        lambda: SuccessfulIngestionService()
    )

    try:
        with TestClient(app) as client:
            response = client.post(
                "/documents/upload",
                data={"workspace_id": "1"},
                files={
                    "file": (
                        "guide.md",
                        b"# Guide\n\nHello TechPilot.\n",
                        "text/markdown",
                    )
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json() == {
        "document_id": 42,
        "filename": "guide.md",
        "status": "COMPLETED",
        "file_type": "markdown",
        "chunk_count": 2,
        "checksum": "a" * 64,
    }


def test_upload_document_returns_404_for_missing_workspace() -> None:
    app.dependency_overrides[get_ingestion_service] = (
        lambda: MissingWorkspaceIngestionService()
    )

    try:
        with TestClient(app) as client:
            response = client.post(
                "/documents/upload",
                data={"workspace_id": "999"},
                files={
                    "file": (
                        "guide.md",
                        b"# Guide\n",
                        "text/markdown",
                    )
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Workspace 999 does not exist."
    }
