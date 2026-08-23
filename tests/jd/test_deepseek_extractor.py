import asyncio
import json

import httpx

from app.jd.deepseek_extractor import DeepSeekJDExtractor


JD_TEXT = "Python is required."


def _response(payload):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(payload)
                    }
                }
            ]
        },
    )


def _valid_payload():
    return {
        "title": "Backend Engineer",
        "company": "Example",
        "requirements": [
            {
                "id": "req-1",
                "raw_text": "Python is required",
                "normalized_skill": "Python",
                "category": "technical",
                "requirement_type": "required",
                "evidence_span": {
                    "text": "Python is required",
                    "start": 0,
                    "end": 18,
                },
            }
        ],
    }


def test_valid_output_does_not_use_model_repair():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return _response(_valid_payload())

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    extractor = DeepSeekJDExtractor(
        api_key="test",
        client=client,
    )

    outcome = asyncio.run(
        extractor.extract_with_metadata(JD_TEXT)
    )
    asyncio.run(client.aclose())

    assert calls == 1
    assert outcome.attempts == 1
    assert outcome.repair_used is False


def test_invalid_semantics_allows_exactly_one_model_repair():
    calls = 0

    invalid = _valid_payload()
    invalid["requirements"][0]["evidence_span"] = {
        "text": "Python is required",
        "start": 1,
        "end": 19,
    }

    async def handler(request):
        nonlocal calls
        calls += 1
        return _response(invalid if calls == 1 else _valid_payload())

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    extractor = DeepSeekJDExtractor(
        api_key="test",
        client=client,
    )

    outcome = asyncio.run(
        extractor.extract_with_metadata(JD_TEXT)
    )
    asyncio.run(client.aclose())

    assert calls == 2
    assert outcome.attempts == 2
    assert outcome.repair_used is True
