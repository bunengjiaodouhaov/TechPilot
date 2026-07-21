import json

import httpx
import pytest

from app.answering.deepseek_llm import (
    DeepSeekLLMProvider,
    DeepSeekLLMProviderError,
)


@pytest.mark.asyncio
async def test_generate_sends_prompts_and_parses_answer() -> None:
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request

        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "text": "PostgreSQL uses MVCC.",
                                    "cited_source_ids": ["SOURCE_1"],
                                    "refused": False,
                                }
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        provider = DeepSeekLLMProvider(
            api_key="test-key",
            client=client,
        )

        result = await provider.generate(
            system_prompt="System rules",
            user_prompt="Question and sources",
        )

    assert result.text == "PostgreSQL uses MVCC."
    assert result.cited_source_ids == ("SOURCE_1",)
    assert result.refused is False

    assert captured_request is not None
    assert captured_request.url == (
        "https://api.deepseek.com/chat/completions"
    )
    assert captured_request.headers["authorization"] == (
        "Bearer test-key"
    )

    request_data = json.loads(captured_request.content)

    assert request_data["model"] == "deepseek-v4-flash"
    assert request_data["messages"] == [
        {
            "role": "system",
            "content": "System rules",
        },
        {
            "role": "user",
            "content": "Question and sources",
        },
    ]
    assert request_data["thinking"] == {"type": "disabled"}
    assert request_data["response_format"] == {
        "type": "json_object"
    }


@pytest.mark.asyncio
async def test_generate_wraps_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=401,
            json={"error": {"message": "Unauthorized"}},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        provider = DeepSeekLLMProvider(
            api_key="invalid-key",
            client=client,
        )

        with pytest.raises(
            DeepSeekLLMProviderError,
            match="DeepSeek request failed",
        ):
            await provider.generate(
                system_prompt="System rules",
                user_prompt="Question and sources",
            )


@pytest.mark.asyncio
async def test_generate_rejects_invalid_answer_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "text": "Answer",
                                    "cited_source_ids": "SOURCE_1",
                                    "refused": False,
                                }
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        provider = DeepSeekLLMProvider(
            api_key="test-key",
            client=client,
        )

        with pytest.raises(
            DeepSeekLLMProviderError,
            match="cited_source_ids",
        ):
            await provider.generate(
                system_prompt="System rules",
                user_prompt="Question and sources",
            )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("api_key", "", "api_key must not be empty"),
        ("base_url", "", "base_url must not be empty"),
        ("model", "", "model must not be empty"),
        (
            "timeout_seconds",
            0,
            "timeout_seconds must be greater than zero",
        ),
    ],
)
def test_constructor_validates_configuration(
    field: str,
    value: object,
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "api_key": "test-key",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "timeout_seconds": 60,
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=message):
        DeepSeekLLMProvider(**arguments)  # type: ignore[arg-type]
