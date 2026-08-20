from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.workspaces import get_workspace_service, router
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

    async def list_workspaces(self) -> list[WorkspaceRecord]:
        return self.items

    async def create_workspace(self, *, name: str) -> WorkspaceRecord:
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


def _client() -> TestClient:
    app = FastAPI()
    service = FakeWorkspaceService()
    app.dependency_overrides[get_workspace_service] = lambda: service
    app.include_router(router)
    return TestClient(app)


def test_workspace_list_and_create() -> None:
    client = _client()

    listed = client.get("/workspaces")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "TechPilot"

    created = client.post("/workspaces", json={"name": "Release review"})
    assert created.status_code == 201
    assert created.json()["id"] == 3
    assert created.json()["name"] == "Release review"


def test_workspace_delete_is_fail_closed_when_sources_remain() -> None:
    client = _client()

    response = client.delete("/workspaces/2")

    assert response.status_code == 409
    assert "active documents" in response.json()["detail"]


def test_workspace_delete_missing_returns_404() -> None:
    client = _client()

    response = client.delete("/workspaces/999")

    assert response.status_code == 404
