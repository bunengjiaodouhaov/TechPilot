from pathlib import Path


def test_answers_preserves_stateless_legacy_path() -> None:
    source = Path("app/api/answers.py").read_text()

    assert "conversation = None" in source
    assert "if request.conversation_id is not None:" in source
    assert "if conversation is not None:" in source
    assert "conversation = Conversation(" not in source


def test_product_ui_explicitly_supplies_conversation_id() -> None:
    source = Path("app/product_ui/static/app.js").read_text()
    assert "conversation_id: await ensureConversationId()" in source
