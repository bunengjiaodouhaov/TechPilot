from typing import Any

import pytest

from app.core.network import should_trust_proxy_environment
from app.retrieval.qdrant_repository import QdrantRepository


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:6333", False),
        ("http://localhost.:6333", False),
        ("http://127.0.0.1:6333", False),
        ("http://[::1]:6333", False),
        ("https://qdrant.example.com", True),
    ],
)
def test_proxy_environment_policy(url: str, expected: bool) -> None:
    assert should_trust_proxy_environment(url) is expected


def test_qdrant_repository_disables_env_proxy_for_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "app.retrieval.qdrant_repository.AsyncQdrantClient",
        FakeClient,
    )

    QdrantRepository(
        qdrant_url="http://localhost:6333",
        collection_name="techpilot_chunks",
        dimension=768,
    )

    assert captured["url"] == "http://localhost:6333"
    assert captured["trust_env"] is False
