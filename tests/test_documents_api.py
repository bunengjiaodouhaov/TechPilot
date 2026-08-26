import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_ingestion_service
from app.auth.dependencies import AuthPrincipal, get_current_user, get_workspace_authorizer
from app.ingestion.service import (
    IngestionResult,
    WorkspaceNotFoundError,
)
from app.main import app


class AllowAuthorizer:
    async def require_access(
        self,
        *,
        user_id: int,
        workspace_id: int,
        owner_required: bool = False,
    ) -> object:
        return object()


@pytest.fixture(autouse=True)
def authenticated_workspace() -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthPrincipal(
        id=7,
        email="test@example.com",
    )
    app.dependency_overrides[get_workspace_authorizer] = lambda: AllowAuthorizer()
    yield
    app.dependency_overrides.clear()


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
        app.dependency_overrides.pop(get_ingestion_service, None)

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
        app.dependency_overrides.pop(get_ingestion_service, None)

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Workspace 999 does not exist."
    }
