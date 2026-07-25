from fastapi.testclient import TestClient

from app.api.dependencies import get_document_service
from app.documents.service import DocumentNotFoundError
from app.main import app


class SuccessfulDocumentService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def delete_document(
        self,
        *,
        workspace_id: int,
        document_id: int,
    ) -> None:
        self.calls.append((workspace_id, document_id))


class MissingDocumentService:
    async def delete_document(
        self,
        *,
        workspace_id: int,
        document_id: int,
    ) -> None:
        raise DocumentNotFoundError(
            f"Document {document_id} does not exist "
            f"in workspace {workspace_id}."
        )


def test_delete_document_returns_204() -> None:
    service = SuccessfulDocumentService()
    app.dependency_overrides[get_document_service] = lambda: service

    try:
        with TestClient(app) as client:
            response = client.delete(
                "/documents/20",
                params={"workspace_id": 10},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert response.content == b""
    assert service.calls == [(10, 20)]


def test_delete_document_returns_404_when_not_found() -> None:
    app.dependency_overrides[get_document_service] = (
        lambda: MissingDocumentService()
    )

    try:
        with TestClient(app) as client:
            response = client.delete(
                "/documents/20",
                params={"workspace_id": 10},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Document 20 does not exist in workspace 10."
    }


def test_delete_document_requires_workspace_id() -> None:
    service = SuccessfulDocumentService()
    app.dependency_overrides[get_document_service] = lambda: service

    try:
        with TestClient(app) as client:
            response = client.delete("/documents/20")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert service.calls == []


def test_delete_document_rejects_non_positive_document_id() -> None:
    service = SuccessfulDocumentService()
    app.dependency_overrides[get_document_service] = lambda: service

    try:
        with TestClient(app) as client:
            response = client.delete(
                "/documents/0",
                params={"workspace_id": 10},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert service.calls == []


def test_delete_document_rejects_non_positive_workspace_id() -> None:
    service = SuccessfulDocumentService()
    app.dependency_overrides[get_document_service] = lambda: service

    try:
        with TestClient(app) as client:
            response = client.delete(
                "/documents/20",
                params={"workspace_id": 0},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert service.calls == []
