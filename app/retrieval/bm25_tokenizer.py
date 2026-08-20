from __future__ import annotations

import re

import jieba


TOKEN_PATTERN = re.compile(
    r"[A-Za-z0-9]+(?:[_.@:/+\-][A-Za-z0-9]+)*"
    r"|[\u4e00-\u9fff]+"
)


def tokenize_for_bm25(text: str) -> list[str]:
    """Tokenize mixed Chinese/technical text for BM25 retrieval."""

    normalized = text.strip()

    if not normalized:
        return []

    tokens: list[str] = []

    for match in TOKEN_PATTERN.finditer(normalized):
        segment = match.group(0)

        if segment[0].isascii():
            tokens.append(segment.lower())
            continue

        tokens.extend(
            token.strip()
            for token in jieba.cut(segment)
            if token.strip()
        )

    return tokens
