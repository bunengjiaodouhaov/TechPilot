from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.document_query_candidate_generate import (
    JsonGenerationResponse,
    _parse_batch_payload,
    build_generation_plan,
)
from scripts.document_query_candidates import QueryGenerationRequest


def _write_anchors(path: Path, count: int) -> None:
    rows = []
    for index in range(count):
        evidence = (
            f"Control {index} requires organizations to document response roles "
            "before incidents occur. This requirement supports consistent preparation."
        )
        rows.append(
            {
                "anchor_id": f"a-{index:03d}",
                "document_key": f"d-{index % 2}",
                "topic": "incident-response",
                "page": index + 1,
                "section": "Preparation",
                "source_unit_sha256": hashlib.sha256(
                    evidence.encode("utf-8")
                ).hexdigest(),
                "evidence_text": evidence * 3,
            }
        )
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in rows),
        encoding="utf-8",
    )


def test_generation_plan_is_reproducible_and_reaches_target(tmp_path: Path) -> None:
    anchors = tmp_path / "anchors.jsonl"
    _write_anchors(anchors, 12)
    first = build_generation_plan(
        anchors_path=anchors,
        output_path=tmp_path / "plan1.jsonl",
        target_count=15,
    )
    second = build_generation_plan(
        anchors_path=anchors,
        output_path=tmp_path / "plan2.jsonl",
        target_count=15,
    )
    assert len(first) == 15
    assert [item.request_id for item in first] == [
        item.request_id for item in second
    ]
    assert len({item.anchor_id for item in first[:12]}) == 12


def test_parse_batch_payload_accepts_grounded_json() -> None:
    evidence = (
        "Organizations should maintain tested incident response plans "
        "and define clear roles before an incident occurs."
    )
    request = QueryGenerationRequest(
        request_id="q1",
        anchor_id="a1",
        document_key="d1",
        topic="incident-response",
        page=1,
        section="Preparation",
        source_unit_sha256=hashlib.sha256(evidence.encode()).hexdigest(),
        evidence_text=evidence,
        requested_category="direct_fact",
        variant="primary",
    )
    payload = {
        "items": [
            {
                "request_id": "q1",
                "category": "direct_fact",
                "usable": True,
                "query": "What should organizations maintain before an incident?",
                "answer_text": "Tested incident response plans.",
                "evidence_quote": "maintain tested incident response plans",
                "reason": "",
            }
        ]
    }
    candidates, errors, unusable = _parse_batch_payload(
        response_payload=payload,
        batch=[request],
        model="fake",
        batch_id="b1",
        repair_count=0,
    )
    assert errors == []
    assert unusable == []
    assert len(candidates) == 1
    assert candidates[0].request_id == "q1"


def test_parse_batch_payload_reports_missing_request() -> None:
    evidence = "A sufficiently long authoritative excerpt about incident response roles and preparation."
    request = QueryGenerationRequest(
        request_id="q1",
        anchor_id="a1",
        document_key="d1",
        topic="incident-response",
        page=1,
        section="Preparation",
        source_unit_sha256=hashlib.sha256(evidence.encode()).hexdigest(),
        evidence_text=evidence * 3,
        requested_category="direct_fact",
        variant="primary",
    )
    _, errors, _ = _parse_batch_payload(
        response_payload={"items": []},
        batch=[request],
        model="fake",
        batch_id="b1",
        repair_count=0,
    )
    assert any("missing request_ids" in item for item in errors)


def test_optional_unusable_rows_can_be_persisted_without_placeholder(tmp_path: Path) -> None:
    from scripts.document_query_candidate_generate import _load_optional_jsonl

    path = tmp_path / "unusable.jsonl"
    path.write_text(
        json.dumps({"request_id": "q1", "reason": "no identifier"}) + "\n",
        encoding="utf-8",
    )
    rows = _load_optional_jsonl(path)
    assert rows == [{"request_id": "q1", "reason": "no identifier"}]

    path.write_text(json.dumps({"empty": True}) + "\n", encoding="utf-8")
    assert _load_optional_jsonl(path) == []


def test_plan_for_480_anchors_has_expected_category_mix(tmp_path: Path) -> None:
    anchors = tmp_path / "anchors-480.jsonl"
    _write_anchors(anchors, 480)
    plan = build_generation_plan(
        anchors_path=anchors,
        output_path=tmp_path / "plan.jsonl",
        target_count=600,
    )
    from collections import Counter
    counts = Counter(item.requested_category for item in plan)
    assert counts == {
        "direct_fact": 190,
        "semantic_paraphrase": 220,
        "keyword_identifier": 110,
        "section_concept": 80,
    }
    assert len(
        {
            item.anchor_id
            for item in plan
            if item.variant == "primary"
        }
    ) == 480


def test_first_eight_primary_requests_cover_all_four_categories(tmp_path: Path) -> None:
    anchors = tmp_path / "anchors-480-mixed.jsonl"
    _write_anchors(anchors, 480)
    plan = build_generation_plan(
        anchors_path=anchors,
        output_path=tmp_path / "plan-mixed.jsonl",
        target_count=600,
    )
    first_eight = [item.requested_category for item in plan[:8]]
    assert set(first_eight) == {
        "direct_fact",
        "semantic_paraphrase",
        "keyword_identifier",
        "section_concept",
    }


@pytest.mark.asyncio
async def test_batch_repair_keeps_valid_items_and_retries_only_unresolved() -> None:
    from scripts.document_query_candidate_generate import (
        JsonGenerationResponse,
        _generate_one_batch,
    )
    import asyncio

    evidence1 = (
        "Organizations should maintain tested incident response plans "
        "before incidents occur."
    ) * 3
    evidence2 = (
        "The organization documents assigned response roles before "
        "an incident occurs."
    ) * 3

    req1 = QueryGenerationRequest(
        request_id="q1",
        anchor_id="a1",
        document_key="d1",
        topic="incident-response",
        page=1,
        section="Preparation",
        source_unit_sha256=hashlib.sha256(evidence1.encode()).hexdigest(),
        evidence_text=evidence1,
        requested_category="direct_fact",
        variant="primary",
    )
    req2 = QueryGenerationRequest(
        request_id="q2",
        anchor_id="a2",
        document_key="d2",
        topic="incident-response",
        page=2,
        section="Roles",
        source_unit_sha256=hashlib.sha256(evidence2.encode()).hexdigest(),
        evidence_text=evidence2,
        requested_category="semantic_paraphrase",
        variant="primary",
    )

    class FakeProvider:
        model = "fake"

        def __init__(self) -> None:
            self.calls = []

        async def generate_json(self, *, system_prompt, user_prompt, max_tokens):
            self.calls.append(user_prompt)
            if len(self.calls) == 1:
                payload = {
                    "items": [
                        {
                            "request_id": "q1",
                            "category": "direct_fact",
                            "usable": True,
                            "query": "What should organizations maintain before incidents occur?",
                            "answer_text": "Tested incident response plans.",
                            "evidence_quote": "maintain tested incident response plans",
                            "reason": "",
                        },
                        {
                            "request_id": "q2",
                            "category": "semantic_paraphrase",
                            "usable": True,
                            "query": "Which roles should be recorded before an incident?",
                            "answer_text": "Assigned response roles.",
                            "evidence_quote": "not present",
                            "reason": "",
                        },
                    ]
                }
            else:
                assert '"q1"' not in user_prompt
                assert '"q2"' in user_prompt
                payload = {
                    "items": [
                        {
                            "request_id": "q2",
                            "category": "semantic_paraphrase",
                            "usable": True,
                            "query": "What responsibilities should be documented before an incident?",
                            "answer_text": "Assigned response roles.",
                            "evidence_quote": "documents assigned response roles",
                            "reason": "",
                        }
                    ]
                }
            return JsonGenerationResponse(
                payload=payload,
                input_tokens=10,
                output_tokens=10,
                latency_ms=1.0,
            )

    provider = FakeProvider()
    candidates, unusable, errors, metrics = await _generate_one_batch(
        provider=provider,
        batch=[req1, req2],
        batch_id="b1",
        max_repairs=1,
        max_tokens=1000,
        semaphore=asyncio.Semaphore(1),
    )

    assert errors == []
    assert unusable == []
    assert [item.request_id for item in candidates] == ["q1", "q2"]
    assert metrics.llm_calls == 2
    assert metrics.repair_calls == 1
    assert len(provider.calls) == 2


def test_plan_metadata_is_written_and_current(tmp_path: Path) -> None:
    from scripts.document_query_candidate_generate import (
        PLAN_VERSION,
        ensure_current_plan,
    )

    anchors = tmp_path / "anchors.jsonl"
    _write_anchors(anchors, 12)
    output = tmp_path / "out"

    requests, meta = ensure_current_plan(
        anchors_path=anchors,
        output_dir=output,
        target_count=15,
    )
    assert len(requests) == 15
    assert meta["plan_version"] == PLAN_VERSION
    assert (output / "query_generation_plan_meta.json").is_file()


def test_stale_plan_without_progress_is_rebuilt(tmp_path: Path) -> None:
    from scripts.document_query_candidate_generate import ensure_current_plan

    anchors = tmp_path / "anchors.jsonl"
    _write_anchors(anchors, 12)
    output = tmp_path / "out"
    output.mkdir()
    (output / "query_generation_requests.jsonl").write_text("{}\n", encoding="utf-8")

    requests, _ = ensure_current_plan(
        anchors_path=anchors,
        output_dir=output,
        target_count=15,
    )
    assert len(requests) == 15


def test_stale_plan_with_progress_fails_closed(tmp_path: Path) -> None:
    from scripts.document_query_candidate_generate import ensure_current_plan
    from scripts.eval_contract import EvaluationContractError

    anchors = tmp_path / "anchors.jsonl"
    _write_anchors(anchors, 12)
    output = tmp_path / "out"
    output.mkdir()
    (output / "query_generation_requests.jsonl").write_text("{}\n", encoding="utf-8")
    (output / "query_candidates_raw.jsonl").write_text(
        '{"candidate_id":"legacy"}\n',
        encoding="utf-8",
    )

    with pytest.raises(EvaluationContractError, match="stale or unversioned"):
        ensure_current_plan(
            anchors_path=anchors,
            output_dir=output,
            target_count=15,
        )


def test_direct_cli_version_works_without_pythonpath() -> None:
    import os
    import subprocess
    import sys

    repository_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            str(repository_root / "scripts/document_query_candidate_generate.py"),
            "--version",
        ],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "document-query-candidates-v6|document-query-plan-v2"
