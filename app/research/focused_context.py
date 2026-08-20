from __future__ import annotations

import json
import re

from app.research.execution_policy import ProfiledUnifiedResearchReasoner


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD = re.compile(r"[A-Za-z0-9_]+")

_STOPWORDS = frozenset(
    {
        "does", "how", "what", "where", "when", "which", "with",
        "from", "into", "that", "this", "the", "and", "for",
        "handling", "enforce", "explain", "show",
    }
)


def query_terms(query: str) -> list[str]:
    expanded = _CAMEL_BOUNDARY.sub(" ", query)
    values: list[str] = []

    for raw in _WORD.findall(expanded):
        value = raw.casefold()
        if len(value) < 4 or value in _STOPWORDS:
            continue
        if value not in values:
            values.append(value)

    return values


def select_query_focused_window(
    *,
    snippet: str,
    query: str,
    budget: int,
) -> tuple[str, int, int]:
    """
    Select a deterministic query-focused character window.

    The budget is unchanged; only which part of the authoritative Evidence is
    exposed to the semantic reasoner changes.
    """
    if budget <= 0:
        raise ValueError("budget must be positive")

    if len(snippet) <= budget:
        return snippet, 0, len(snippet)

    lowered = snippet.casefold()
    hits: list[int] = []

    for term in query_terms(query):
        start = 0
        while True:
            position = lowered.find(term, start)
            if position < 0:
                break
            hits.append(position)
            start = position + max(len(term), 1)

    if not hits:
        return snippet[:budget], 0, budget

    # Median hit is robust when the query term appears several times in the
    # same implementation. Bias the window slightly forward because code often
    # defines the operation before its error/result handling.
    hits.sort()
    anchor = hits[len(hits) // 2]
    before = budget // 3
    start = max(anchor - before, 0)
    end = min(start + budget, len(snippet))

    if end - start < budget:
        start = max(end - budget, 0)

    return snippet[start:end], start, end


class QueryFocusedProfiledReasoner(ProfiledUnifiedResearchReasoner):
    """
    Same model and same character budget as ProfiledUnifiedResearchReasoner,
    but evidence context is query-focused instead of prefix-only.
    """

    def _build_user_prompt(self, state) -> str:
        pack = state.get("evidence_pack")
        task = state.get("normalized_task", state["query"])

        evidence = []
        issues = []

        if pack is not None:
            for item in pack.evidence[: self._profile.max_evidence_items]:
                visible, start, end = select_query_focused_window(
                    snippet=item.snippet,
                    query=task,
                    budget=self._profile.evidence_snippet_characters,
                )
                evidence.append(
                    {
                        "file_path": item.file_path,
                        "symbol": item.symbol,
                        "line_start": item.line_start,
                        "line_end": item.line_end,
                        "snippet": visible,
                        "snippet_window_char_start": start,
                        "snippet_window_char_end": end,
                        "full_snippet_characters": len(item.snippet),
                    }
                )

            issues = [
                issue.model_dump(mode="json")
                for issue in pack.issues[:5]
            ]

        last_action = state.get("last_action")
        last_tool_result = state.get("last_tool_result")

        payload = {
            "task": task,
            "execution_profile": {
                "route": self._profile.route.value,
                "model_tier": self._profile.model_tier.value,
                "max_steps": self._profile.max_steps,
                "max_evidence_items": self._profile.max_evidence_items,
                "evidence_snippet_characters": (
                    self._profile.evidence_snippet_characters
                ),
                "evidence_selection": "query_focused_window",
            },
            "step_count": state.get("step_count", 0),
            "max_steps": state.get("max_steps", self._profile.max_steps),
            "retry_count": state.get("retry_count", 0),
            "max_retries": state.get(
                "max_retries",
                self._profile.max_retries,
            ),
            "last_action": (
                last_action.model_dump()
                if last_action is not None
                else None
            ),
            "last_tool_result": (
                {
                    "ok": last_tool_result.ok,
                    "error_code": (
                        last_tool_result.error_code.value
                        if last_tool_result.error_code is not None
                        else None
                    ),
                    "truncated": last_tool_result.truncated,
                    "data_keys": sorted(
                        (last_tool_result.data or {}).keys()
                    ),
                }
                if last_tool_result is not None
                else None
            ),
            "evidence": evidence,
            "evidence_issues": issues,
            "allowed_capabilities": self._capabilities,
        }

        return (
            "Current agent state JSON:\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + "\n\nReturn one decision JSON."
        )
