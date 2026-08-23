from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from .extractor import (
    JDExtractionValidationError,
    bounded_structural_repair,
    validate_structured_jd,
)
from .prompts import JD_EXTRACTION_SYSTEM_PROMPT
from .schemas import StructuredJD


class JDExtractionError(RuntimeError):
    """Raised when the JD provider cannot produce a valid grounded result."""


class JDExtractionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structured_jd: StructuredJD
    attempts: int
    repair_used: bool
    latency_ms: float


class DeepSeekJDExtractor:
    """DeepSeek adapter for the provider-neutral JD extraction contract."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash",
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._model = model.strip()
        self._timeout_seconds = timeout_seconds
        self._client = client

    async def extract(self, jd_text: str) -> StructuredJD:
        return (await self.extract_with_metadata(jd_text)).structured_jd

    async def extract_with_metadata(self, jd_text: str) -> JDExtractionOutcome:
        normalized = jd_text.strip()
        if not normalized:
            raise ValueError("jd_text must not be empty")

        started = time.perf_counter()
        first_payload = await self._generate(jd_text=jd_text, repair_message=None)

        try:
            structured, structural_repair_used = self._validate_with_structural_repair(
                jd_text=jd_text,
                payload=first_payload,
            )
            return JDExtractionOutcome(
                structured_jd=structured,
                attempts=1,
                repair_used=structural_repair_used,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        except JDExtractionValidationError as first_error:
            repair_message = (
                "The previous JSON violated the schema or evidence binding. "
                "Repair it once without adding new requirements. Validation error: "
                f"{first_error}. Previous JSON: "
                f"{json.dumps(first_payload, ensure_ascii=False)}"
            )

        second_payload = await self._generate(
            jd_text=jd_text,
            repair_message=repair_message,
        )

        try:
            structured, _ = self._validate_with_structural_repair(
                jd_text=jd_text,
                payload=second_payload,
            )
        except JDExtractionValidationError as exc:
            raise JDExtractionError(
                "JD extraction failed after one bounded repair"
            ) from exc

        return JDExtractionOutcome(
            structured_jd=structured,
            attempts=2,
            repair_used=True,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _validate_with_structural_repair(
        self,
        *,
        jd_text: str,
        payload: dict[str, Any],
    ) -> tuple[StructuredJD, bool]:
        try:
            return validate_structured_jd(jd_text=jd_text, payload=payload), False
        except JDExtractionValidationError:
            repaired = bounded_structural_repair(payload)
            if repaired == payload:
                raise
            return (
                validate_structured_jd(jd_text=jd_text, payload=repaired),
                True,
            )

    async def _generate(
        self,
        *,
        jd_text: str,
        repair_message: str | None,
    ) -> dict[str, Any]:
        schema_hint = StructuredJD.model_json_schema()
        user_sections = [
            "StructuredJD JSON Schema:",
            json.dumps(schema_hint, ensure_ascii=False),
            "Original JD:",
            jd_text,
        ]
        if repair_message:
            user_sections.extend(["Repair instruction:", repair_message])

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": JD_EXTRACTION_SYSTEM_PROMPT.strip()},
                {"role": "user", "content": "\n\n".join(user_sections)},
            ],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }

        try:
            if self._client is not None:
                response = await self._post(client=self._client, payload=payload)
            else:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await self._post(client=client, payload=payload)
        except httpx.HTTPError as exc:
            raise JDExtractionError("DeepSeek JD extraction request failed") from exc

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("message content is empty")
            decoded = json.loads(content)
            if not isinstance(decoded, dict):
                raise TypeError("JD output must be a JSON object")
            return decoded
        except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError) as exc:
            raise JDExtractionError("DeepSeek returned invalid JD JSON") from exc

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
