import json
from typing import Any

import httpx

from app.answering.dto import LLMAnswer


class DeepSeekLLMProviderError(RuntimeError):
    """Raised when DeepSeek cannot return a valid provider-neutral answer."""


class DeepSeekLLMProvider:
    """Call DeepSeek through its OpenAI-compatible Chat Completions API."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash",
        timeout_seconds: float = 60.0,
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

        self._api_key = normalized_api_key
        self._base_url = normalized_base_url
        self._model = normalized_model
        self._timeout_seconds = timeout_seconds
        self._client = client

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMAnswer:
        normalized_system_prompt = system_prompt.strip()
        normalized_user_prompt = user_prompt.strip()

        if not normalized_system_prompt:
            raise ValueError("system_prompt must not be empty")

        if not normalized_user_prompt:
            raise ValueError("user_prompt must not be empty")

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": normalized_system_prompt,
                },
                {
                    "role": "user",
                    "content": normalized_user_prompt,
                },
            ],
            "thinking": {
                "type": "disabled",
            },
            "response_format": {
                "type": "json_object",
            },
            "temperature": 0,
        }

        try:
            if self._client is not None:
                response = await self._post(
                    client=self._client,
                    payload=payload,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                ) as client:
                    response = await self._post(
                        client=client,
                        payload=payload,
                    )
        except httpx.HTTPError as exc:
            raise DeepSeekLLMProviderError(
                "DeepSeek request failed"
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
    def _parse_response(
        response: httpx.Response,
    ) -> LLMAnswer:
        try:
            response_data = response.json()
            content = response_data["choices"][0]["message"]["content"]

            if not isinstance(content, str) or not content.strip():
                raise ValueError("message content is empty")

            answer_data = json.loads(content)
        except (
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise DeepSeekLLMProviderError(
                "DeepSeek returned an invalid response"
            ) from exc

        text = answer_data.get("text")
        cited_source_ids = answer_data.get("cited_source_ids")
        refused = answer_data.get("refused")

        if not isinstance(text, str):
            raise DeepSeekLLMProviderError(
                "DeepSeek answer field 'text' must be a string"
            )

        if (
            not isinstance(cited_source_ids, list)
            or not all(
                isinstance(source_id, str)
                for source_id in cited_source_ids
            )
        ):
            raise DeepSeekLLMProviderError(
                "DeepSeek answer field 'cited_source_ids' "
                "must be an array of strings"
            )

        if not isinstance(refused, bool):
            raise DeepSeekLLMProviderError(
                "DeepSeek answer field 'refused' must be a boolean"
            )

        return LLMAnswer(
            text=text.strip(),
            cited_source_ids=tuple(cited_source_ids),
            refused=refused,
        )
