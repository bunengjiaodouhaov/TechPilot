import json

import httpx
import pytest

from app.answering.deepseek_evidence_verifier import (
    DeepSeekEvidenceVerifierError,
    DeepSeekEvidenceVerifierProvider,
)
from app.answering.evidence_dto import (
    EvidenceItem,
    EvidenceReason,
    EvidenceState,
    EvidenceVerificationInput,
)
from app.prompts.evidence_verifier import (
    EVIDENCE_VERIFIER_PROMPT_VERSION,
    EVIDENCE_VERIFIER_SYSTEM_PROMPT,
)


def make_request() -> EvidenceVerificationInput:
    return EvidenceVerificationInput(
        target="Which embedding model does TechPilot use?",
        evidence=(
            EvidenceItem(
                source_id="SOURCE_1",
                text="TechPilot uses multilingual-e5-base for dense retrieval.",
                source_type="document",
                source_ref="chunk-1",
                title="techpilot.md",
                locator="section=Retrieval",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_verify_sends_evidence_prompt_and_parses_sufficient_result() -> None:
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
                                    "state": "sufficient",
                                    "reasons": [],
                                    "supporting_source_ids": ["SOURCE_1"],
                                    "conflicting_source_ids": [],
                                    "explanation": "SOURCE_1 directly supports the target.",
                                }
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekEvidenceVerifierProvider(
            api_key="test-key",
            client=client,
        )
        result = await provider.verify(request=make_request())

    assert result.state is EvidenceState.SUFFICIENT
    assert result.reasons == ()
    assert result.supporting_source_ids == ("SOURCE_1",)
    assert result.conflicting_source_ids == ()

    assert captured_request is not None
    request_data = json.loads(captured_request.content)
    assert request_data["messages"][0]["content"] == EVIDENCE_VERIFIER_SYSTEM_PROMPT
    assert "Which embedding model does TechPilot use?" in request_data["messages"][1]["content"]
    assert "[SOURCE_1]" in request_data["messages"][1]["content"]
    assert "multilingual-e5-base" in request_data["messages"][1]["content"]
    assert request_data["thinking"] == {"type": "disabled"}
    assert request_data["response_format"] == {"type": "json_object"}
    assert request_data["temperature"] == 0


@pytest.mark.asyncio
async def test_verify_returns_no_evidence_without_network_call() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("network call must not happen")

    request = EvidenceVerificationInput(
        target="Unknown target",
        evidence=(),
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekEvidenceVerifierProvider(
            api_key="test-key",
            client=client,
        )
        result = await provider.verify(request=request)

    assert calls == 0
    assert result.state is EvidenceState.INSUFFICIENT
    assert result.reasons == (EvidenceReason.NO_EVIDENCE,)
    assert result.supporting_source_ids == ()
    assert result.conflicting_source_ids == ()


@pytest.mark.asyncio
async def test_verify_rejects_unknown_source_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "state": "sufficient",
                                    "reasons": [],
                                    "supporting_source_ids": ["SOURCE_99"],
                                    "conflicting_source_ids": [],
                                    "explanation": "Unsupported source.",
                                }
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekEvidenceVerifierProvider(
            api_key="test-key",
            client=client,
        )
        with pytest.raises(
            DeepSeekEvidenceVerifierError,
            match="unknown sources: SOURCE_99",
        ):
            await provider.verify(request=make_request())


@pytest.mark.asyncio
async def test_verify_rejects_inconsistent_sufficient_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "state": "sufficient",
                                    "reasons": ["relation_missing"],
                                    "supporting_source_ids": ["SOURCE_1"],
                                    "conflicting_source_ids": [],
                                    "explanation": "Inconsistent result.",
                                }
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekEvidenceVerifierProvider(
            api_key="test-key",
            client=client,
        )
        with pytest.raises(
            DeepSeekEvidenceVerifierError,
            match="invalid evidence verifier response",
        ):
            await provider.verify(request=make_request())


@pytest.mark.asyncio
async def test_verify_parses_conflicting_result() -> None:
    request = EvidenceVerificationInput(
        target="Which model is configured?",
        evidence=(
            EvidenceItem(
                source_id="SOURCE_1",
                text="The configured model is A.",
                source_type="document",
                source_ref="chunk-1",
                title="a.md",
            ),
            EvidenceItem(
                source_id="SOURCE_2",
                text="The configured model is B.",
                source_type="document",
                source_ref="chunk-2",
                title="b.md",
            ),
        ),
    )

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "state": "conflicting",
                                    "reasons": ["conflicting_evidence"],
                                    "supporting_source_ids": [],
                                    "conflicting_source_ids": ["SOURCE_1", "SOURCE_2"],
                                    "explanation": "The sources disagree about the configured model.",
                                }
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekEvidenceVerifierProvider(api_key="test-key", client=client)
        result = await provider.verify(request=request)

    assert result.state is EvidenceState.CONFLICTING
    assert result.reasons == (EvidenceReason.CONFLICTING_EVIDENCE,)
    assert result.conflicting_source_ids == ("SOURCE_1", "SOURCE_2")


@pytest.mark.asyncio
async def test_verify_wraps_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, json={"error": "failure"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekEvidenceVerifierProvider(api_key="test-key", client=client)
        with pytest.raises(
            DeepSeekEvidenceVerifierError,
            match="evidence verification request failed",
        ):
            await provider.verify(request=make_request())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("api_key", "", "api_key must not be empty"),
        ("base_url", "", "base_url must not be empty"),
        ("model", "", "model must not be empty"),
        ("timeout_seconds", 0, "timeout_seconds must be greater than zero"),
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
        DeepSeekEvidenceVerifierProvider(**arguments)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_verify_rejects_extra_response_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "state": "sufficient",
                                    "reasons": [],
                                    "supporting_source_ids": ["SOURCE_1"],
                                    "conflicting_source_ids": [],
                                    "explanation": "Supported.",
                                    "confidence": 0.99,
                                }
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekEvidenceVerifierProvider(api_key="test-key", client=client)
        with pytest.raises(
            DeepSeekEvidenceVerifierError,
            match="invalid evidence verifier response",
        ):
            await provider.verify(request=make_request())


@pytest.mark.asyncio
async def test_verify_rejects_no_evidence_reason_when_evidence_exists() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "state": "insufficient",
                                    "reasons": ["no_evidence"],
                                    "supporting_source_ids": [],
                                    "conflicting_source_ids": [],
                                    "explanation": "Incorrect no-evidence claim.",
                                }
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekEvidenceVerifierProvider(api_key="test-key", client=client)
        with pytest.raises(
            DeepSeekEvidenceVerifierError,
            match="no_evidence reason is invalid",
        ):
            await provider.verify(request=make_request())


def test_evidence_verifier_prompt_has_stable_version() -> None:
    assert EVIDENCE_VERIFIER_PROMPT_VERSION == "evidence-verifier-v2"


def test_evidence_verifier_prompt_defines_non_cascading_reason_taxonomy() -> None:
    from app.prompts.evidence_verifier import EVIDENCE_VERIFIER_SYSTEM_PROMPT

    assert "single minimal decisive reason" in EVIDENCE_VERIFIER_SYSTEM_PROMPT
    assert (
        "do not also report attribute_missing or relation_missing"
        in EVIDENCE_VERIFIER_SYSTEM_PROMPT
    )
    assert (
        "reasons must contain exactly one primary reason"
        in EVIDENCE_VERIFIER_SYSTEM_PROMPT
    )
