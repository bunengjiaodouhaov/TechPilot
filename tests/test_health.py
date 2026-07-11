from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "techpilot",
    }

def test_dependencies_health() -> None:
    response = client.get("/health/dependencies")

    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"

    dependencies = body["dependencies"]

    assert dependencies["postgres"]["status"] == "ok"
    assert dependencies["redis"]["status"] == "ok"
    assert dependencies["qdrant"]["status"] == "ok"

    assert "latency_ms" in dependencies["postgres"]
    assert "latency_ms" in dependencies["redis"]
    assert "latency_ms" in dependencies["qdrant"]