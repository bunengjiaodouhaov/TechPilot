from fastapi.testclient import TestClient

from app.main import app


def test_document_upload_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/documents/upload",
            data={"workspace_id": "1"},
            files={
                "file": (
                    "guide.md",
                    b"# Guide\n",
                    "text/markdown",
                )
            },
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication required"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_answer_api_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/answers",
            json={
                "workspace_id": 1,
                "question": "What is TechPilot?",
            },
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication required"}
    assert response.headers["www-authenticate"] == "Bearer"
