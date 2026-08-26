from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.workspaces import get_workspace_service, router
from app.auth.dependencies import AuthPrincipal, get_current_user, get_workspace_authorizer
from app.workspaces.service import WorkspaceNotEmptyError, WorkspaceNotFoundError


@dataclass
class WorkspaceRecord:
    id: int
    name: str
    created_at: datetime
    updated_at: datetime


class FakeWorkspaceService:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.items = [WorkspaceRecord(2, "TechPilot", now, now)]
        self.last_user_id: int | None = None
        self.last_owner_id: int | None = None

    async def list_workspaces(self, *, user_id: int) -> list[WorkspaceRecord]:
        self.last_user_id = user_id
        return self.items

    async def create_workspace(
        self,
        *,
        name: str,
        owner_user_id: int,
    ) -> WorkspaceRecord:
        self.last_owner_id = owner_user_id
        now = datetime.now(timezone.utc)
        created = WorkspaceRecord(3, name, now, now)
        self.items.append(created)
        return created

    async def delete_workspace(self, *, workspace_id: int) -> None:
        if workspace_id == 2:
            raise WorkspaceNotEmptyError("Workspace still contains active documents.")
        if workspace_id == 999:
            raise WorkspaceNotFoundError("Workspace 999 does not exist.")
        self.items = [item for item in self.items if item.id != workspace_id]


class AllowAuthorizer:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, bool]] = []

    async def require_access(
        self,
        *,
        user_id: int,
        workspace_id: int,
        owner_required: bool = False,
    ) -> object:
        self.calls.append((user_id, workspace_id, owner_required))
        return object()


def _client() -> tuple[TestClient, FakeWorkspaceService, AllowAuthorizer]:
    app = FastAPI()
    service = FakeWorkspaceService()
    authorizer = AllowAuthorizer()
    app.dependency_overrides[get_workspace_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: AuthPrincipal(
        id=7,
        email="owner@example.com",
    )
    app.dependency_overrides[get_workspace_authorizer] = lambda: authorizer
    app.include_router(router)
    return TestClient(app), service, authorizer


def test_workspace_list_and_create_are_scoped_to_principal() -> None:
    client, service, _ = _client()

    listed = client.get("/workspaces")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "TechPilot"
    assert service.last_user_id == 7

    created = client.post("/workspaces", json={"name": "Release review"})
    assert created.status_code == 201
    assert created.json()["id"] == 3
    assert created.json()["name"] == "Release review"
    assert service.last_owner_id == 7


def test_workspace_delete_requires_owner_before_service_call() -> None:
    client, _, authorizer = _client()

    response = client.delete("/workspaces/2")

    assert response.status_code == 409
    assert "active documents" in response.json()["detail"]
    assert authorizer.calls == [(7, 2, True)]


def test_workspace_delete_missing_returns_404() -> None:
    client, _, _ = _client()

    response = client.delete("/workspaces/999")

    assert response.status_code == 404
