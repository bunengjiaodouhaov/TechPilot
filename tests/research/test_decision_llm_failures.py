from __future__ import annotations

import httpx
import pytest

from app.research.contracts import DecisionFailureCode
from app.research.decision_llm import (
    DeepSeekResearchDecisionProvider,
    ResearchDecisionProviderError,
)


def _provider(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekResearchDecisionProvider(
        api_key="test-key",
        client=client,
        timeout_seconds=0.1,
    )
    return provider, client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (429, DecisionFailureCode.RATE_LIMITED, True),
        (500, DecisionFailureCode.UPSTREAM_ERROR, True),
        (503, DecisionFailureCode.UPSTREAM_ERROR, True),
        (401, DecisionFailureCode.AUTH_ERROR, False),
        (403, DecisionFailureCode.AUTH_ERROR, False),
        (400, DecisionFailureCode.REQUEST_ERROR, False),
    ],
)
async def test_http_status_failure_is_classified(
    status_code,
    expected_code,
    retryable,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, request=request)

    provider, client = _provider(handler)
    try:
        with pytest.raises(ResearchDecisionProviderError) as caught:
            await provider.generate_json(
                system_prompt="system",
                user_prompt="user",
            )
        assert caught.value.code is expected_code
        assert caught.value.retryable is retryable
        assert caught.value.status_code == status_code
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_timeout_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("injected timeout", request=request)

    provider, client = _provider(handler)
    try:
        with pytest.raises(ResearchDecisionProviderError) as caught:
            await provider.generate_json(
                system_prompt="system",
                user_prompt="user",
            )
        assert caught.value.code is DecisionFailureCode.TIMEOUT
        assert caught.value.retryable is True
        assert caught.value.status_code is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_invalid_response_is_permanent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "not-json"}}]},
        )

    provider, client = _provider(handler)
    try:
        with pytest.raises(ResearchDecisionProviderError) as caught:
            await provider.generate_json(
                system_prompt="system",
                user_prompt="user",
            )
        assert caught.value.code is DecisionFailureCode.INVALID_RESPONSE
        assert caught.value.retryable is False
    finally:
        await client.aclose()
