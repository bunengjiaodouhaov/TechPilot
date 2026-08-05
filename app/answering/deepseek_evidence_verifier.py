from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError

from app.answering.evidence_dto import (
    EvidenceReason,
    EvidenceState,
    EvidenceVerificationInput,
    EvidenceVerificationResult,
)
from app.answering.evidence_verifier import (
    build_evidence_verification_prompt,
    validate_evidence_verification_result,
)
from app.prompts.evidence_verifier import EVIDENCE_VERIFIER_SYSTEM_PROMPT


class DeepSeekEvidenceVerifierError(RuntimeError):
    """Raised when DeepSeek cannot return a valid evidence decision."""


class DeepSeekEvidenceVerifierProvider:
    """Verify evidence through DeepSeek's OpenAI-compatible chat API."""

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

    async def verify(
        self,
        *,
        request: EvidenceVerificationInput,
    ) -> EvidenceVerificationResult:
        user_prompt = build_evidence_verification_prompt(request=request)

        if not request.evidence:
            return EvidenceVerificationResult(
                state=EvidenceState.INSUFFICIENT,
                reasons=(EvidenceReason.NO_EVIDENCE,),
                supporting_source_ids=(),
                conflicting_source_ids=(),
                explanation="No evidence was supplied for verification.",
            )

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": EVIDENCE_VERIFIER_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }

        try:
            if self._client is not None:
                response = await self._post(client=self._client, payload=payload)
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                ) as client:
                    response = await self._post(client=client, payload=payload)
        except httpx.HTTPError as exc:
            raise DeepSeekEvidenceVerifierError(
                "DeepSeek evidence verification request failed"
            ) from exc

        return self._parse_response(response=response, request=request)

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
        *,
        response: httpx.Response,
        request: EvidenceVerificationInput,
    ) -> EvidenceVerificationResult:
        try:
            response_data = response.json()
            content = response_data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("message content is empty")
            result = EvidenceVerificationResult.model_validate_json(content)
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            raise DeepSeekEvidenceVerifierError(
                "DeepSeek returned an invalid evidence verifier response"
            ) from exc

        try:
            validate_evidence_verification_result(
                request=request,
                result=result,
            )
        except ValueError as exc:
            raise DeepSeekEvidenceVerifierError(str(exc)) from exc

        return result
