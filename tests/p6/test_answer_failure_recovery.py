from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.api.answers as answers_module
from app.answering.dto import Answer
from app.api.dependencies import get_answer_service, get_db_session
from app.auth.dependencies import (
    AuthPrincipal,
    get_current_user,
    get_idempotency_service,
    get_workspace_authorizer,
)
from app.auth.idempotency import IdempotencyDecision
from app.conversations.concurrency import ConversationWriteConflict
from app.main import app
from app.models.conversation import Conversation


class AllowAuthorizer:
    async def require_access(
        self,
        *,
        user_id: int,
        workspace_id: int,
        owner_required: bool = False,
    ) -> object:
        return object()


class FakeScalars:
    def all(self) -> list[Any]:
        return []


class FakeSession:
    def __init__(self, *, conversation: Conversation | None = None) -> None:
        self.conversation = conversation
        self.commit_count = 0
        self.rollback_count = 0
        self.added: list[Any] = []

    async def get(self, model: type[Any], object_id: int) -> Any:
        if model is Conversation and self.conversation is not None:
            if self.conversation.id == object_id:
                return self.conversation
        return None

    async def scalars(self, statement: Any) -> FakeScalars:
        return FakeScalars()

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    def add(self, value: Any) -> None:
        self.added.append(value)

    def add_all(self, values: list[Any]) -> None:
        self.added.extend(values)


class StatefulIdempotency:
    def __init__(self) -> None:
        self.state = "NEW"
        self.request_hash: str | None = None
        self.fail_count = 0
        self.complete_count = 0

    async def begin(
        self,
        *,
        user_id: int,
        workspace_id: int,
        operation: str,
        key: str,
        request_hash: str,
    ) -> IdempotencyDecision:
        if self.request_hash is None:
            self.request_hash = request_hash
        elif self.request_hash != request_hash:
            raise AssertionError("test reused key with a different payload")
        if self.state == "PROCESSING":
            raise AssertionError("duplicate in-flight request in test")
        self.state = "PROCESSING"
        return IdempotencyDecision(record_id=11)

    async def fail(self, *, record_id: int) -> None:
        assert record_id == 11
        self.state = "FAILED"
        self.fail_count += 1

    async def complete(
        self,
        *,
        record_id: int,
        status_code: int,
        response_json: dict[str, Any],
    ) -> None:
        assert record_id == 11
        self.state = "COMPLETED"
        self.complete_count += 1

    async def stage_complete(
        self,
        *,
        record_id: int,
        status_code: int,
        response_json: dict[str, Any],
    ) -> None:
        assert record_id == 11
        self.state = "COMPLETED"
        self.complete_count += 1


class FailOnceAnswerService:
    def __init__(self) -> None:
        self.calls = 0

    async def answer(
        self,
        *,
        question: str,
        workspace_id: int,
        retrieval_limit: int = 5,
    ) -> Answer:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated provider failure after bounded retries")
        return Answer(
            question=question,
            text="recovered answer",
            citations=(),
            refused=False,
        )


class AlwaysFailAnswerService:
    async def answer(
        self,
        *,
        question: str,
        workspace_id: int,
        retrieval_limit: int = 5,
    ) -> Answer:
        raise RuntimeError("simulated provider failure after bounded retries")


class SuccessfulAnswerService:
    async def answer(
        self,
        *,
        question: str,
        workspace_id: int,
        retrieval_limit: int = 5,
    ) -> Answer:
        return Answer(
            question=question,
            text="generated from the captured conversation version",
            citations=(),
            refused=False,
        )


@pytest.fixture(autouse=True)
def reset_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _install_common_overrides(*, idempotency: StatefulIdempotency) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthPrincipal(
        id=7,
        email="p6@example.com",
    )
    app.dependency_overrides[get_workspace_authorizer] = lambda: AllowAuthorizer()
    app.dependency_overrides[get_idempotency_service] = lambda: idempotency


def test_provider_failure_marks_idempotency_failed_and_same_key_can_retry() -> None:
    service = FailOnceAnswerService()
    idempotency = StatefulIdempotency()
    _install_common_overrides(idempotency=idempotency)
    app.dependency_overrides[get_answer_service] = lambda: service

    payload = {"workspace_id": 179, "question": "failure recovery probe"}
    headers = {"Idempotency-Key": "p6-provider-failure"}

    with TestClient(app, raise_server_exceptions=False) as client:
        failed = client.post("/answers", json=payload, headers=headers)
        assert failed.status_code == 500
        assert idempotency.state == "FAILED"
        assert idempotency.fail_count == 1
        assert idempotency.complete_count == 0

        retried = client.post("/answers", json=payload, headers=headers)

    assert retried.status_code == 200
    assert retried.json()["answer"] == "recovered answer"
    assert idempotency.state == "COMPLETED"
    assert idempotency.complete_count == 1
    assert service.calls == 2


def test_provider_failure_with_conversation_writes_no_half_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = Conversation(
        id=23,
        workspace_id=179,
        title="existing",
        version=4,
    )
    session = FakeSession(conversation=conversation)
    idempotency = StatefulIdempotency()
    _install_common_overrides(idempotency=idempotency)
    app.dependency_overrides[get_answer_service] = lambda: AlwaysFailAnswerService()

    async def fake_session_dependency() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_db_session] = fake_session_dependency

    async def append_must_not_run(**kwargs: Any) -> None:
        raise AssertionError("conversation append ran after provider failure")

    monkeypatch.setattr(answers_module, "append_exchange_if_version", append_must_not_run)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/answers",
            headers={"Idempotency-Key": "p6-conversation-provider-failure"},
            json={
                "workspace_id": 179,
                "conversation_id": 23,
                "question": "probe",
            },
        )

    assert response.status_code == 500
    assert session.commit_count == 1  # release read tx before the slow answer path
    assert session.added == []
    assert conversation.version == 4
    assert idempotency.state == "FAILED"


def test_cas_conflict_marks_idempotency_failed_without_staging_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = Conversation(
        id=23,
        workspace_id=179,
        title="existing",
        version=4,
    )
    session = FakeSession(conversation=conversation)
    idempotency = StatefulIdempotency()
    _install_common_overrides(idempotency=idempotency)
    app.dependency_overrides[get_answer_service] = lambda: SuccessfulAnswerService()

    async def fake_session_dependency() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_db_session] = fake_session_dependency

    async def stale_append(**kwargs: Any) -> None:
        raise ConversationWriteConflict("stale version")

    monkeypatch.setattr(answers_module, "append_exchange_if_version", stale_append)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/answers",
            headers={"Idempotency-Key": "p6-cas-failure"},
            json={
                "workspace_id": 179,
                "conversation_id": 23,
                "question": "probe",
            },
        )

    assert response.status_code == 409
    assert "conversation changed" in response.json()["detail"]
    assert session.added == []
    assert conversation.version == 4
    assert idempotency.state == "FAILED"
    assert idempotency.fail_count == 1
