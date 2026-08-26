from __future__ import annotations

import httpx
import pytest

from app.answering.provider_retry import (
    RetryingEvidenceVerifierProvider,
    RetryingLLMProvider,
)


class FlakyVerifier:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def verify(self, *, request):
        self.calls += 1
        if self.calls <= self.failures:
            req = httpx.Request("POST", "https://example.test")
            resp = httpx.Response(429, request=req)
            err = httpx.HTTPStatusError(
                "rate limited",
                request=req,
                response=resp,
            )
            raise RuntimeError("wrapped provider failure") from err
        return "ok"


class BadRequestVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, *, request):
        self.calls += 1
        req = httpx.Request("POST", "https://example.test")
        resp = httpx.Response(400, request=req)
        err = httpx.HTTPStatusError(
            "bad request",
            request=req,
            response=resp,
        )
        raise RuntimeError("wrapped provider failure") from err


class AlwaysTimeoutVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, *, request):
        self.calls += 1
        req = httpx.Request("POST", "https://example.test")
        err = httpx.ReadTimeout("provider timeout", request=req)
        raise RuntimeError("wrapped provider timeout") from err


class FlakyLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *, system_prompt, user_prompt):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("DeepSeek returned an invalid response")
        return "ok"


@pytest.mark.asyncio
async def test_verifier_retries_transient_http_failure() -> None:
    provider = FlakyVerifier(failures=2)
    wrapper = RetryingEvidenceVerifierProvider(
        provider=provider,
        max_attempts=3,
        base_delay_seconds=0,
    )

    assert await wrapper.verify(request=object()) == "ok"
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_verifier_exhausts_bounded_timeout_retries_and_propagates() -> None:
    provider = AlwaysTimeoutVerifier()
    wrapper = RetryingEvidenceVerifierProvider(
        provider=provider,
        max_attempts=3,
        base_delay_seconds=0,
    )

    with pytest.raises(RuntimeError, match="wrapped provider timeout"):
        await wrapper.verify(request=object())

    assert provider.calls == 3


@pytest.mark.asyncio
async def test_verifier_does_not_retry_non_transient_4xx() -> None:
    provider = BadRequestVerifier()
    wrapper = RetryingEvidenceVerifierProvider(
        provider=provider,
        max_attempts=3,
        base_delay_seconds=0,
    )

    with pytest.raises(RuntimeError):
        await wrapper.verify(request=object())

    assert provider.calls == 1


@pytest.mark.asyncio
async def test_llm_retries_structured_output_once() -> None:
    provider = FlakyLLM()
    wrapper = RetryingLLMProvider(
        provider=provider,
        max_attempts=3,
        base_delay_seconds=0,
    )

    assert (
        await wrapper.generate(
            system_prompt="system",
            user_prompt="user",
        )
        == "ok"
    )
    assert provider.calls == 2
