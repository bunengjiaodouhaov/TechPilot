from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from app.research.contracts import DecisionFailureCode


class ResearchDecisionProviderError(RuntimeError):
    """Structured provider failure; retry policy remains outside the provider."""

    def __init__(
        self,
        message: str,
        *,
        code: DecisionFailureCode,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class ResearchDecisionProvider(Protocol):
    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        ...


class DeepSeekResearchDecisionProvider:
    """Research-only JSON decision client for DeepSeek Chat Completions."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash",
        timeout_seconds: float = 60.0,
        max_tokens: int = 1200,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_api_key = api_key.strip()
        normalized_base_url = base_url.rstrip("/")
        normalized_model = model.strip()

        if not normalized_api_key:
            raise ValueError("api_key must not be empty")
        if not normalized_base_url:
            raise ValueError("base_url must not be empty")
        if not normalized_model:
            raise ValueError("model must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be greater than zero")

        self._api_key = normalized_api_key
        self._base_url = normalized_base_url
        self._model = normalized_model
        self._timeout_seconds = timeout_seconds
        self._max_tokens = max_tokens
        self._client = client

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        normalized_system = system_prompt.strip()
        normalized_user = user_prompt.strip()
        if not normalized_system:
            raise ValueError("system_prompt must not be empty")
        if not normalized_user:
            raise ValueError("user_prompt must not be empty")

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": normalized_system},
                {"role": "user", "content": normalized_user},
            ],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": self._max_tokens,
        }

        try:
            if self._client is not None:
                response = await self._post(client=self._client, payload=payload)
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                ) as client:
                    response = await self._post(
                        client=client,
                        payload=payload,
                    )
        except httpx.TimeoutException as exc:
            raise ResearchDecisionProviderError(
                "DeepSeek research decision request timed out",
                code=DecisionFailureCode.TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                code = DecisionFailureCode.RATE_LIMITED
                retryable = True
            elif 500 <= status_code <= 599:
                code = DecisionFailureCode.UPSTREAM_ERROR
                retryable = True
            elif status_code in {401, 403}:
                code = DecisionFailureCode.AUTH_ERROR
                retryable = False
            else:
                code = DecisionFailureCode.REQUEST_ERROR
                retryable = False

            raise ResearchDecisionProviderError(
                "DeepSeek research decision request failed",
                code=code,
                retryable=retryable,
                status_code=status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise ResearchDecisionProviderError(
                "DeepSeek research decision network request failed",
                code=DecisionFailureCode.NETWORK_ERROR,
                retryable=True,
            ) from exc

        return self._parse_response(response)

    async def _post(
        self,
        *,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> httpx.Response:
        response = await client.post(
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        return response

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        try:
            response_data = response.json()
            content = response_data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("message content is empty")
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise TypeError("decision JSON must be an object")
            return parsed
        except (
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise ResearchDecisionProviderError(
                "DeepSeek returned an invalid research decision",
                code=DecisionFailureCode.INVALID_RESPONSE,
                retryable=False,
            ) from exc
