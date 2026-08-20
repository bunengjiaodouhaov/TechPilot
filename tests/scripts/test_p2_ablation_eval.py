from scripts.p2_ablation_eval import (
    GitIdentity,
    build_report,
    legacy_nonempty_gate_allows,
    summarize_evidence_gate,
    summarize_retrieval_ablation,
)


def test_legacy_nonempty_gate_uses_evidence_presence():
    assert legacy_nonempty_gate_allows({"evidence": [{"source_id": "S1"}]})
    assert not legacy_nonempty_gate_allows({"evidence": []})


def test_legacy_nonempty_gate_falls_back_to_no_evidence_reason():
    assert not legacy_nonempty_gate_allows(
        {"expected_primary_reason": "no_evidence"}
    )
    assert legacy_nonempty_gate_allows(
        {"expected_primary_reason": "subject_mismatch"}
    )


def test_evidence_ablation_separates_unsafe_accept_from_over_refusal():
    rows = [
        {
            "case": {
                "expected_state": "sufficient",
                "evidence": [{"source_id": "S1"}],
            },
            "actual": {"state": "sufficient"},
        },
        {
            "case": {
                "expected_state": "insufficient",
                "evidence": [{"source_id": "S2"}],
            },
            "actual": {"state": "insufficient"},
        },
        {
            "case": {
                "expected_state": "conflicting",
                "evidence": [
                    {"source_id": "S3"},
                    {"source_id": "S4"},
                ],
            },
            "actual": {"state": "conflicting"},
        },
    ]

    summary = summarize_evidence_gate(rows)

    assert summary["state_accuracy"] == 1.0
    assert summary["legacy_nonempty_gate"]["unsafe_accepts"] == 2
    assert summary["legacy_nonempty_gate"]["over_refusals"] == 0
    assert summary["evidence_verifier_gate"]["unsafe_accepts"] == 0
    assert summary["evidence_verifier_gate"]["over_refusals"] == 0
    assert summary["cost_proxy"]["generator_calls_avoided_by_verifier"] == 2


def test_retrieval_ablation_computes_component_deltas():
    summary = {
        "metrics": {
            "dense": {"recall_at_k": 0.7, "mrr_at_k": 0.5, "misses": 9},
            "bm25": {"recall_at_k": 0.7, "mrr_at_k": 0.56, "misses": 9},
            "hybrid": {"recall_at_k": 0.76, "mrr_at_k": 0.58, "misses": 7},
            "hybrid_reranker": {
                "recall_at_k": 0.86,
                "mrr_at_k": 0.76,
                "misses": 4,
            },
        },
        "failure_analysis": {
            "rescues": [{"case_index": 1}],
            "regressions": [],
            "retained_hybrid_hits": 23,
            "rerank_candidate_miss": [{"case_index": 2}],
        },
        "latency": {"rerank_added": {"mean_ms": 2300.0}},
    }

    ablation = summarize_retrieval_ablation(summary)

    assert round(
        ablation["gains"]["reranker_vs_hybrid"]["recall_at_k_delta"],
        6,
    ) == 0.1
    assert ablation["reranker_behavior"]["rescues"] == 1
    assert ablation["reranker_behavior"]["regressions"] == 0


def test_dirty_git_sha_is_labeled_as_baseline_only(tmp_path):
    retrieval_summary = {
        "metrics": {
            "dense": {"recall_at_k": 0.7, "mrr_at_k": 0.5, "misses": 9},
            "bm25": {"recall_at_k": 0.7, "mrr_at_k": 0.56, "misses": 9},
            "hybrid": {"recall_at_k": 0.76, "mrr_at_k": 0.58, "misses": 7},
            "hybrid_reranker": {
                "recall_at_k": 0.86,
                "mrr_at_k": 0.76,
                "misses": 4,
            },
        },
        "failure_analysis": {
            "rescues": [],
            "regressions": [],
            "retained_hybrid_hits": 23,
            "rerank_candidate_miss": [],
        },
        "latency": {"rerank_added": {"mean_ms": 2300.0}},
        "integrity": {},
        "config": {},
    }
    evidence_rows = [
        {
            "case": {
                "expected_state": "sufficient",
                "evidence": [{"source_id": "S1"}],
            },
            "actual": {"state": "sufficient"},
        }
    ]

    report = build_report(
        retrieval_summary=retrieval_summary,
        evidence_rows=evidence_rows,
        trace_id="trace-1",
        config_version="cfg-1",
        git_identity=GitIdentity(sha="abc123", dirty=True),
        retrieval_summary_path=tmp_path / "retrieval.json",
        evidence_results_path=tmp_path / "evidence.jsonl",
    )

    assert report["trace"]["git_sha"] == "abc123"
    assert report["trace"]["git_dirty"] is True
    assert (
        report["trace"]["git_sha_semantics"]
        == "baseline_commit_only_when_git_dirty"
    )
