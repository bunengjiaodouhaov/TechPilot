from app.job.query_parser import QueryParser


def test_query_parser_normalizes_known_entities_without_guessing():
    spec = QueryParser().parse(
        "上海 AI Engineer，偏 LLM RAG，30k+"
    )

    assert spec.location == "Shanghai"
    assert spec.role == "AI Engineer"
    assert spec.salary_min == 30000
    assert "LLM" in spec.domains
    assert "RAG" in spec.domains
