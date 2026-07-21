import pytest

from app.answering.llm import SYSTEM_PROMPT, build_user_prompt


def test_build_user_prompt_contains_question_and_context() -> None:
    result = build_user_prompt(
        question="  PostgreSQL 如何实现事务隔离？  ",
        prompt_context=(
            "  [SOURCE_1]\n"
            "document: postgresql.pdf\n"
            "content:\n"
            "PostgreSQL uses MVCC.  "
        ),
    )

    assert "Question:\n\nPostgreSQL 如何实现事务隔离？" in result
    assert "[SOURCE_1]" in result
    assert "PostgreSQL uses MVCC." in result


def test_build_user_prompt_requires_source_identifiers() -> None:
    result = build_user_prompt(
        question="What is MVCC?",
        prompt_context="[SOURCE_1]\ncontent:\nEvidence",
    )

    assert "exact SOURCE_N identifiers" in result


def test_system_prompt_forbids_outside_knowledge() -> None:
    assert "Answer only from the supplied sources" in SYSTEM_PROMPT
    assert "Do not use outside knowledge" in SYSTEM_PROMPT
    assert "Never invent a source identifier" in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "question",
    [
        "",
        "   ",
    ],
)
def test_build_user_prompt_rejects_empty_question(
    question: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="question must not be empty",
    ):
        build_user_prompt(
            question=question,
            prompt_context="[SOURCE_1]\ncontent:\nEvidence",
        )


@pytest.mark.parametrize(
    "prompt_context",
    [
        "",
        "   ",
    ],
)
def test_build_user_prompt_rejects_empty_context(
    prompt_context: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="prompt_context must not be empty",
    ):
        build_user_prompt(
            question="Valid question",
            prompt_context=prompt_context,
        )
