from fastapi.testclient import TestClient

from app.answering.dto import Answer, Citation
from app.api.dependencies import get_answer_service
from app.main import app


class FakeAnswerService:
    def __init__(self, answer: Answer) -> None:
        self._answer = answer
        self.calls: list[dict[str, object]] = []

    async def answer(
        self,
        *,
        question: str,
        workspace_id: int,
        retrieval_limit: int = 5,
    ) -> Answer:
        self.calls.append(
            {
                "question": question,
                "workspace_id": workspace_id,
                "retrieval_limit": retrieval_limit,
            }
        )
        return self._answer


def test_answer_question_returns_answer_and_public_citations() -> None:
    service = FakeAnswerService(
        Answer(
            question="FastAPI 的作用是什么？",
            text="FastAPI 用于构建 Python API。",
            citations=(
                Citation(
                    chunk_id="internal-chunk-id",
                    document_id=123,
                    document_name="backend.md",
                    page_start=None,
                    page_end=None,
                    section="FastAPI",
                    quote="FastAPI is a web framework.",
                ),
            ),
            refused=False,
        )
    )

    app.dependency_overrides[get_answer_service] = lambda: service

    try:
        with TestClient(app) as client:
            response = client.post(
                "/answers",
                json={
                    "workspace_id": 1,
                    "question": "FastAPI 的作用是什么？",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "question": "FastAPI 的作用是什么？",
        "answer": "FastAPI 用于构建 Python API。",
        "citations": [
            {
                "document_name": "backend.md",
                "page_start": None,
                "page_end": None,
                "section": "FastAPI",
                "quote": "FastAPI is a web framework.",
            }
        ],
        "refused": False,
    }

    assert service.calls == [
        {
            "question": "FastAPI 的作用是什么？",
            "workspace_id": 1,
            "retrieval_limit": 5,
        }
    ]

    body = response.json()
    assert "chunk_id" not in body["citations"][0]
    assert "document_id" not in body["citations"][0]


def test_answer_question_returns_business_refusal_as_200() -> None:
    service = FakeAnswerService(
        Answer(
            question="项目中没有答案的问题",
            text="现有文档中没有足够证据回答这个问题。",
            citations=(),
            refused=True,
        )
    )

    app.dependency_overrides[get_answer_service] = lambda: service

    try:
        with TestClient(app) as client:
            response = client.post(
                "/answers",
                json={
                    "workspace_id": 1,
                    "question": "项目中没有答案的问题",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "question": "项目中没有答案的问题",
        "answer": "现有文档中没有足够证据回答这个问题。",
        "citations": [],
        "refused": True,
    }


def test_answer_question_strips_question_whitespace() -> None:
    service = FakeAnswerService(
        Answer(
            question="什么是向量检索？",
            text="向量检索通过语义相似度查找内容。",
            citations=(),
            refused=False,
        )
    )

    app.dependency_overrides[get_answer_service] = lambda: service

    try:
        with TestClient(app) as client:
            response = client.post(
                "/answers",
                json={
                    "workspace_id": 3,
                    "question": "  什么是向量检索？  ",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert service.calls[0]["question"] == "什么是向量检索？"


def test_answer_question_rejects_blank_question() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/answers",
            json={
                "workspace_id": 1,
                "question": "   ",
            },
        )

    assert response.status_code == 422


def test_answer_question_rejects_invalid_workspace_id() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/answers",
            json={
                "workspace_id": 0,
                "question": "什么是 RAG？",
            },
        )

    assert response.status_code == 422


def test_answer_question_returns_404_when_workspace_is_missing() -> None:
    from app.answering.answer_service import WorkspaceNotFoundError

    class MissingWorkspaceAnswerService:
        async def answer(
            self,
            *,
            question: str,
            workspace_id: int,
            retrieval_limit: int = 5,
        ) -> Answer:
            raise WorkspaceNotFoundError(
                f"workspace {workspace_id} was not found"
            )

    app.dependency_overrides[get_answer_service] = (
        lambda: MissingWorkspaceAnswerService()
    )

    try:
        with TestClient(app) as client:
            response = client.post(
                "/answers",
                json={
                    "workspace_id": 999,
                    "question": "什么是 RAG？",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {
        "detail": "workspace not found",
    }


def test_answer_question_returns_404_when_workspace_is_missing() -> None:
    from app.answering.answer_service import WorkspaceNotFoundError

    class MissingWorkspaceAnswerService:
        async def answer(
            self,
            *,
            question: str,
            workspace_id: int,
            retrieval_limit: int = 5,
        ) -> Answer:
            raise WorkspaceNotFoundError(
                f"workspace {workspace_id} was not found"
            )

    app.dependency_overrides[get_answer_service] = (
        lambda: MissingWorkspaceAnswerService()
    )

    try:
        with TestClient(app) as client:
            response = client.post(
                "/answers",
                json={
                    "workspace_id": 999,
                    "question": "什么是 RAG？",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {
        "detail": "workspace not found",
    }


def test_answer_question_returns_404_when_workspace_is_missing() -> None:
    from app.answering.answer_service import WorkspaceNotFoundError

    class MissingWorkspaceAnswerService:
        async def answer(
            self,
            *,
            question: str,
            workspace_id: int,
            retrieval_limit: int = 5,
        ) -> Answer:
            raise WorkspaceNotFoundError(
                f"workspace {workspace_id} was not found"
            )

    app.dependency_overrides[get_answer_service] = (
        lambda: MissingWorkspaceAnswerService()
    )

    try:
        with TestClient(app) as client:
            response = client.post(
                "/answers",
                json={
                    "workspace_id": 999,
                    "question": "什么是 RAG？",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {
        "detail": "workspace not found",
    }
