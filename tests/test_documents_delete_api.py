import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_document_service
from app.auth.dependencies import AuthPrincipal, get_current_user, get_workspace_authorizer
from app.documents.service import DocumentNotFoundError
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
        app.dependency_overrides.pop(get_document_service, None)

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
        app.dependency_overrides.pop(get_document_service, None)

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
        app.dependency_overrides.pop(get_document_service, None)

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
        app.dependency_overrides.pop(get_document_service, None)

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
        app.dependency_overrides.pop(get_document_service, None)

    assert response.status_code == 422
    assert service.calls == []
