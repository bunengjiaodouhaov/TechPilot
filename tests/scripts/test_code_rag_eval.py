import pytest
import json

from scripts.code_rag_eval import (
    CaseMetrics,
    CodeRagEvaluationCase,
    ExpectedEvidence,
    evidence_content_hit,
    exact_symbol_hit,
    file_compression_ratio,
    file_noise_rate,
    first_expected_file_rank,
    load_cases,
    noise_file_count,
    raw_tool_request,
    summarize,
)


def test_load_cases_rejects_duplicate_ids(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    payload = {
        "case_id": "dup",
        "category": "symbol",
        "query": "RepoExplorer",
        "task_intent": "locate explorer",
        "search_mode": "symbol",
        "limit": 5,
        "expected_evidence": [
            {
                "file_path": "app/repository/repo_explorer.py",
                "symbol": "RepoExplorer",
            }
        ],
        "expected_incomplete": False,
    }
    path.write_text(
        json.dumps(payload) + "\n" + json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    try:
        load_cases(path)
    except ValueError as exc:
        assert "duplicate case_id" in str(exc)
    else:
        raise AssertionError("duplicate case ids must fail")


def test_file_metrics_distinguish_absolute_noise_and_ratio() -> None:
    paths = ["a.py", "a.py", "b.py", "c.py"]

    assert first_expected_file_rank(paths, {"b.py"}) == 2
    assert noise_file_count(paths, {"b.py"}) == 2
    assert file_noise_rate(paths, {"b.py"}) == 2 / 3
    assert file_compression_ratio(paths, ["b.py"]) == pytest.approx(2 / 3)


def test_content_hit_does_not_require_exact_symbol_granularity() -> None:
    expected = (
        ExpectedEvidence(
            file_path="app/code_index.py",
            symbol="Chunker._stable_id",
            contains="hashlib.sha256",
        ),
    )
    evidence = [
        {
            "file_path": "app/code_index.py",
            "symbol": "Chunker",
            "snippet": "def _stable_id(...):\n    return hashlib.sha256(data).hexdigest()",
        }
    ]

    assert evidence_content_hit(expected=expected, evidence=evidence) is True
    assert exact_symbol_hit(expected=expected, evidence=evidence) is False


def test_exact_symbol_is_not_scored_when_golden_has_no_symbol() -> None:
    expected = (
        ExpectedEvidence(
            file_path="app/evidence_pack.py",
            contains="CodeEvidence",
        ),
    )
    evidence = [
        {
            "file_path": "app/evidence_pack.py",
            "symbol": None,
            "snippet": "from app.code_evidence import CodeEvidence",
        }
    ]

    assert exact_symbol_hit(expected=expected, evidence=evidence) is None


def test_raw_tool_request_passes_query_to_module_structure_search() -> None:
    case = CodeRagEvaluationCase(
        case_id="module-1",
        category="module",
        query="EvidencePack CodeEvidence",
        task_intent="inspect module structure",
        search_mode="module",
        limit=20,
        expected_evidence=(
            ExpectedEvidence(file_path="app/harness/evidence_pack.py"),
        ),
        expected_incomplete=None,
    )

    assert raw_tool_request(case) == (
        "inspect_modules",
        {"query": "EvidencePack CodeEvidence", "limit": 20},
    )


def test_summary_separates_content_symbol_noise_and_compression() -> None:
    cases = [
        CodeRagEvaluationCase(
            case_id="a",
            category="symbol",
            query="A",
            task_intent="find A",
            search_mode="symbol",
            limit=5,
            expected_evidence=(ExpectedEvidence(file_path="a.py", symbol="A"),),
            expected_incomplete=False,
        ),
        CodeRagEvaluationCase(
            case_id="b",
            category="symbol",
            query="B",
            task_intent="find B",
            search_mode="symbol",
            limit=5,
            expected_evidence=(ExpectedEvidence(file_path="b.py", symbol="B"),),
            expected_incomplete=True,
        ),
    ]
    metrics = [
        CaseMetrics(
            raw_file_rank=1,
            explorer_file_rank=1,
            evidence_content_hit=True,
            exact_symbol_hit=True,
            provenance_integrity=True,
            incomplete=False,
            incomplete_correct=True,
            raw_unique_files=4,
            evidence_unique_files=1,
            raw_noise_files=3,
            evidence_noise_files=0,
            raw_file_noise_rate=0.75,
            evidence_file_noise_rate=0.0,
            file_compression_ratio=0.75,
        ),
        CaseMetrics(
            raw_file_rank=None,
            explorer_file_rank=None,
            evidence_content_hit=False,
            exact_symbol_hit=False,
            provenance_integrity=True,
            incomplete=False,
            incomplete_correct=False,
            raw_unique_files=3,
            evidence_unique_files=2,
            raw_noise_files=3,
            evidence_noise_files=2,
            raw_file_noise_rate=1.0,
            evidence_file_noise_rate=1.0,
            file_compression_ratio=1 / 3,
        ),
    ]

    summary = summarize(cases=cases, metrics=metrics)

    assert summary["raw_file_hit_rate"] == 0.5
    assert summary["evidence_content_hit_rate"] == 0.5
    assert summary["exact_symbol_hit_rate"] == 0.5
    assert summary["provenance_integrity_rate"] == 1.0
    assert summary["incomplete_expectation_accuracy"] == 0.5
    assert summary["raw_noise_files_mean"] == 3
    assert summary["evidence_noise_files_mean"] == 1
