from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.product_ui.router import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_frontend_home_is_served() -> None:
    response = _client().get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "TechPilot" in response.text
    assert "Ask the system." in response.text
    assert "Your workspaces" in response.text
    assert 'id="workspaceNameInput"' in response.text
    assert 'type="number"' not in response.text
    assert "/ui/app.js" in response.text


def test_frontend_assets_are_served() -> None:
    client = _client()

    stylesheet = client.get("/ui/styles.css")
    script = client.get("/ui/app.js")

    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert "--bg: #dce6f2" in stylesheet.text
    assert "brighter translucent gray-blue" in stylesheet.text
    assert script.status_code == 200
    assert 'fetch("/workspaces"' in script.text
    assert "createWorkspace" in script.text
    assert "deleteWorkspace" in script.text
    assert "submitQuestion" in script.text
