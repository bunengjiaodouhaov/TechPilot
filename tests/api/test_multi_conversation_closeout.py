from pathlib import Path
from app.main import app


def test_multi_conversation_routes_are_public() -> None:
    paths = set(app.openapi().get("paths", {}))
    assert "/workspaces/{workspace_id}/conversations" in paths
    assert "/conversations/{conversation_id}/history" in paths
    assert "/conversations/{conversation_id}" in paths


def test_answer_contract_accepts_conversation_id() -> None:
    schema = app.openapi()["components"]["schemas"]["AnswerRequest"]
    assert "conversation_id" in schema["properties"]


def test_ui_loads_conversation_controls() -> None:
    html = Path("app/product_ui/static/index.html").read_text()
    assert "/ui/conversation-ui.js" in html
