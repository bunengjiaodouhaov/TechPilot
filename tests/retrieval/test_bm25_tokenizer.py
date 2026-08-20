from app.retrieval.bm25_tokenizer import tokenize_for_bm25


def test_tokenizer_preserves_technical_terms() -> None:
    tokens = tokenize_for_bm25(
        "FastAPI 的 UploadFile 使用 SpooledTemporaryFile"
    )

    assert "fastapi" in tokens
    assert "uploadfile" in tokens
    assert "spooledtemporaryfile" in tokens


def test_tokenizer_preserves_complex_technical_tokens() -> None:
    tokens = tokenize_for_bm25(
        "workspace_id Recall@5 multilingual-e5-base SHA-256"
    )

    assert tokens == [
        "workspace_id",
        "recall@5",
        "multilingual-e5-base",
        "sha-256",
    ]


def test_tokenizer_preserves_numeric_terms() -> None:
    tokens = tokenize_for_bm25(
        "HTTP 500 Python 3.11"
    )

    assert tokens == [
        "http",
        "500",
        "python",
        "3.11",
    ]


def test_tokenizer_handles_chinese_text() -> None:
    tokens = tokenize_for_bm25(
        "超过阈值后文件会写入磁盘"
    )

    assert "文件" in tokens
    assert "磁盘" in tokens


def test_tokenizer_normalizes_technical_term_case() -> None:
    assert tokenize_for_bm25(
        "UploadFile"
    ) == ["uploadfile"]

    assert tokenize_for_bm25(
        "UPLOADFILE"
    ) == ["uploadfile"]


def test_tokenizer_ignores_empty_text() -> None:
    assert tokenize_for_bm25("   ") == []
