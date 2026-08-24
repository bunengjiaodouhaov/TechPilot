from fastapi.testclient import TestClient

from app.main import app


def _openapi_paths() -> set[str]:
    return set(app.openapi().get("paths", {}))


def test_product_exposes_repository_persistence_and_history_routes() -> None:
    paths = _openapi_paths()
    assert "/repository/status" in paths
    assert "/repository/reindex" in paths
    assert "/repository/query" in paths
    assert "/workspaces/{workspace_id}/documents" in paths
    assert "/workspaces/{workspace_id}/history" in paths


def test_product_shell_loads_code_rag_and_memory_assets() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "/ui/code-rag.js" in response.text
    assert "/ui/product-memory.js" in response.text
    assert "/ui/code-rag.css" in response.text
