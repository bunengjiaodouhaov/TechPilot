from __future__ import annotations

import asyncio
from typing import Protocol

import httpx

from app.answering.dto import LLMAnswer
from app.answering.evidence_dto import (
    EvidenceVerificationInput,
    EvidenceVerificationResult,
)


def _find_http_error(exc: BaseException) -> httpx.HTTPError | None:
    current: BaseException | None = exc
    seen: set[int] = set()

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPError):
            return current
        current = current.__cause__ or current.__context__

    return None


def _is_retryable_http_error(exc: httpx.HTTPError) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {
            408,
            409,
            425,
            429,
            500,
            502,
            503,
            504,
        }

    return isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ),
    )


def _is_structured_output_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "invalid evidence verifier response",
            "invalid response",
            "must be a string",
            "must be an array of strings",
            "must be a boolean",
        )
    )


async def _sleep(attempt: int, base_delay_seconds: float) -> None:
    delay = base_delay_seconds * (2 ** (attempt - 1))
    if delay > 0:
        await asyncio.sleep(delay)


class EvidenceVerifierProtocol(Protocol):
    async def verify(
        self,
        *,
        request: EvidenceVerificationInput,
    ) -> EvidenceVerificationResult:
        ...


class LLMProviderProtocol(Protocol):
    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMAnswer:
        ...


class RetryingEvidenceVerifierProvider:
    def __init__(
        self,
        *,
        provider: EvidenceVerifierProtocol,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.5,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")
        if base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be non-negative")

        self._provider = provider
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds

    async def verify(
        self,
        *,
        request: EvidenceVerificationInput,
    ) -> EvidenceVerificationResult:
        structured_retry_used = False

        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self._provider.verify(request=request)
            except Exception as exc:
                http_error = _find_http_error(exc)

                if (
                    http_error is not None
                    and _is_retryable_http_error(http_error)
                    and attempt < self._max_attempts
                ):
                    await _sleep(attempt, self._base_delay_seconds)
                    continue

                if (
                    http_error is None
                    and not structured_retry_used
                    and _is_structured_output_error(exc)
                    and attempt < self._max_attempts
                ):
                    structured_retry_used = True
                    await _sleep(attempt, self._base_delay_seconds)
                    continue

                raise

        raise RuntimeError("unreachable retry state")


class RetryingLLMProvider:
    def __init__(
        self,
        *,
        provider: LLMProviderProtocol,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.5,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")
        if base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be non-negative")

        self._provider = provider
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMAnswer:
        structured_retry_used = False

        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self._provider.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
            except Exception as exc:
                http_error = _find_http_error(exc)

                if (
                    http_error is not None
                    and _is_retryable_http_error(http_error)
                    and attempt < self._max_attempts
                ):
                    await _sleep(attempt, self._base_delay_seconds)
                    continue

                if (
                    http_error is None
                    and not structured_retry_used
                    and _is_structured_output_error(exc)
                    and attempt < self._max_attempts
                ):
                    structured_retry_used = True
                    await _sleep(attempt, self._base_delay_seconds)
                    continue

                raise

        raise RuntimeError("unreachable retry state")
