from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExecutionRoute(StrEnum):
    WORKFLOW = "workflow"
    LIGHT_AGENT = "light_agent"
    RESEARCH_AGENT = "research_agent"


class ModelTier(StrEnum):
    NONE = "none"
    MEDIUM = "medium"
    LARGE = "large"


class RoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: ExecutionRoute
    model_tier: ModelTier
    reason: str
    signals: list[str] = Field(default_factory=list)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be empty")
        return normalized


class HeuristicTaskRouter:
    """
    Transparent Day32 baseline.

    This router does not claim to solve semantic routing generally. It provides
    a deterministic, inspectable baseline that can later be compared with an
    LLM router using route_correctness / latency / cost.
    """

    _workflow_patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "explicit-read-file",
            re.compile(
                r"\b(read|open|show|cat)\b.{0,32}\b(file|path)\b",
                re.IGNORECASE,
            ),
        ),
        (
            "explicit-tree",
            re.compile(
                r"\b(list|show)\b.{0,24}\b(files|tree|directory)\b",
                re.IGNORECASE,
            ),
        ),
        (
            "explicit-symbol-search",
            re.compile(
                r"\b(find|search|locate)\b.{0,24}\b(symbol|class|function|method)\b",
                re.IGNORECASE,
            ),
        ),
        (
            "explicit-code-search",
            re.compile(
                r"\b(search|grep|find)\b.{0,24}\b(code|literal|string|text)\b",
                re.IGNORECASE,
            ),
        ),
        (
            "cn-explicit-workflow",
            re.compile(
                r"(读取|打开|显示).{0,12}(文件|路径)|"
                r"(列出|显示).{0,12}(目录|文件树|文件列表)|"
                r"(查找|搜索|定位).{0,12}(符号|类|函数|方法)",
                re.IGNORECASE,
            ),
        ),
    )

    _research_signals: tuple[tuple[str, re.Pattern[str], int], ...] = (
        (
            "multi-part",
            re.compile(
                r"\b(both|multiple|several|respectively|across)\b|"
                r"(同时|分别|多个|跨)",
                re.IGNORECASE,
            ),
            2,
        ),
        (
            "comparison",
            re.compile(
                r"\b(compare|comparison|versus|vs\.?|trade-?offs?)\b|"
                r"(比较|对比|权衡)",
                re.IGNORECASE,
            ),
            2,
        ),
        (
            "cross-source-verification",
            re.compile(
                r"\b(verify|cross-check|cross source|multi-source|"
                r"documentation and (?:source|repo)|docs and repo)\b|"
                r"(核实|验证|多源|文档.{0,8}(源码|仓库)|"
                r"(源码|仓库).{0,8}文档)",
                re.IGNORECASE,
            ),
            2,
        ),
        (
            "conflict-resolution",
            re.compile(
                r"\b(conflict|contradict|disagree|inconsisten)\w*\b|"
                r"(冲突|矛盾|不一致)",
                re.IGNORECASE,
            ),
            3,
        ),
        (
            "architecture-why",
            re.compile(
                r"\b(why|architecture|end-to-end|design rationale|"
                r"root cause)\b|"
                r"(为什么|架构|端到端|设计原因|根因)",
                re.IGNORECASE,
            ),
            1,
        ),
    )

    _camel_case_symbol = re.compile(
        r"\b[A-Z][A-Za-z0-9]+(?:[A-Z][A-Za-z0-9]*)*\b"
    )

    def route(self, query: str) -> RoutingDecision:
        normalized = " ".join(query.strip().split())
        if not normalized:
            raise ValueError("query must not be empty")

        workflow_signal = self._match_workflow(normalized)
        if workflow_signal is not None:
            return RoutingDecision(
                route=ExecutionRoute.WORKFLOW,
                model_tier=ModelTier.NONE,
                reason=(
                    "The request specifies a deterministic repository operation "
                    "with a known execution path; agent reasoning is unnecessary."
                ),
                signals=[workflow_signal],
            )

        score = 0
        signals: list[str] = []
        for name, pattern, weight in self._research_signals:
            if pattern.search(normalized):
                signals.append(name)
                score += weight

        symbols = {
            value
            for value in self._camel_case_symbol.findall(normalized)
            if value not in {"How", "What", "Where", "Using", "Explain"}
        }
        if len(symbols) >= 2:
            signals.append("multiple-named-symbols")
            score += 2

        if score >= 2:
            return RoutingDecision(
                route=ExecutionRoute.RESEARCH_AGENT,
                model_tier=ModelTier.LARGE,
                reason=(
                    "The task has multi-part, cross-source, comparative, "
                    "conflict-resolution, or otherwise high-complexity research "
                    "signals that justify a larger bounded research budget."
                ),
                signals=signals,
            )

        light_signals = signals or ["single-focused-semantic-task"]
        if symbols:
            light_signals.append("named-symbol")

        return RoutingDecision(
            route=ExecutionRoute.LIGHT_AGENT,
            model_tier=ModelTier.MEDIUM,
            reason=(
                "The task requires semantic interpretation or dynamic evidence "
                "selection, but remains narrow enough for a small bounded agent "
                "loop rather than full research escalation."
            ),
            signals=light_signals,
        )

    def _match_workflow(self, query: str) -> str | None:
        for name, pattern in self._workflow_patterns:
            if pattern.search(query):
                return name
        return None
