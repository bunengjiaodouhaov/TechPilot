# ADR-001: Thin Agent / Thick Harness Runtime Boundary

- Status: Accepted
- Date: 2026-08-04
- Scope: TechPilot v1 architecture boundary
- Implementation status: Design freeze only; no Agent Runtime implementation in Day 13

## Context

TechPilot 当前主线仍是 RAG / Code RAG / JD Evidence。P2 的优先级不变：Dense、BM25、RRF Hybrid、Reranker、拒答、Evidence Verifier、Evaluation Gate。

Day 13 新增 Harness 设计约束的目的，是减少未来 P3/P4 重构，不是提前启动 Coding Agent 或重写现有检索链路。

因此从 Day 13 起，架构理解冻结为：

```text
TechPilot
= RAG / Code RAG capabilities
+ Tool Runtime
+ Context Engineering
+ Evidence / Verification
+ Trace / Evaluation
+ Agent Control Layer
```

LangGraph 可以承担 Agent Control Layer，但不是 TechPilot 的核心业务资产。

## Decision

采用 **Thin Agent + Thick Harness**。

### Thin Agent

Agent Control Layer 只负责有限控制决策：

```text
clarify / normalize task
        ↓
plan limited subproblems
        ↓
select next capability / tool
        ↓
execute through Harness
        ↓
inspect Context / Evidence state
        ↓
continue / finalize
        ↓
max-steps / termination guard
```

Thin Agent 可以负责：
- 任务澄清与标准化
- 有限子问题规划
- 选择下一步 capability / tool
- 根据 Evidence 决定继续或结束
- 有限重试、最大步骤和终止控制

Thin Agent 不负责：
- Dense / BM25 / Hybrid / Reranker 内部实现
- Tool 业务实现
- Context 存储与压缩细节
- Evidence Verifier 内部实现
- Trace 持久化细节
- Repository 读写实现

这些能力必须可以脱离 Agent 控制循环独立测试。

### Thick Harness

Harness 承载可复用、可测试、可观测的执行基础设施：

```text
Tool Runtime
Context Engineering
Evidence / Verification
Trace / Evaluation
Permission Boundary
```

#### Tool Runtime

未来统一负责结构化输入输出、工具发现与调用、超时、有限重试、结构化错误、风险等级和 Trace metadata。

Day 13 不实现 Tool Registry，也不冻结完整 ToolContract 字段；字段留到 Day 14。

#### Context

Context 层负责控制哪些信息进入模型调用、哪些工具结果保留、Evidence 去重、Token budget 和长输出截断。

P2 不建设复杂 Context Manager；没有 Token / 延迟 / 质量指标支撑的复杂压缩策略不进入 v1。

#### Evidence / Verification

关键结论必须绑定实际 Evidence。

Evidence 不足或冲突时，应继续检索 / 改写或明确返回不足原因；不能用模型自身“置信度”替代证据验证。

#### Trace / Evaluation

Trace 是 Harness 的横切能力，目标是回答：

```text
一次错误答案具体错在哪一步？
```

未来至少区分 retrieval、tool call、evidence check、model call、agent decision、finalization。

Day 13 不实现 Agent Event Log。P2 只逐步标准化现有 evaluation / trace 字段。

#### Permission Boundary

权限边界属于 Harness，而不是依赖 Prompt 自律。

TechPilot v1 保持只读，不开放：
- edit_file
- shell
- arbitrary command execution
- git write operations
- worktree modification
- automatic code modification

## v1 Boundary

v1 的 Agent 能力冻结为：

```text
Research
Understand
Analyze
Plan
```

系统可以检索文档和代码、阅读文件、分析仓库、汇总 Evidence、判断 Evidence 是否充分、输出技术分析和改造计划，并请求人工确认。

人工确认只代表批准计划或任务，不意味着自动执行代码修改。

## v2 Boundary

以下能力进入 v2 backlog：

```text
Action
Modify
Verify through execution
```

可能包括 edit_file、run_tests、shell、git diff、worktree、repository write operations、automated repair loop。

这些不是当前 sprint，也不是 v1 验收条件。

## LangGraph Boundary

LangGraph 只承担控制层职责：
- State
- conditional edges
- workflow recovery
- termination
- bounded control flow

LangGraph 不承载 Dense、BM25、Hybrid/RRF、Reranker、Tool 业务逻辑、Evidence Verifier 或 Context Manager 的核心实现。

## Phase Boundary

### P2 — Day 13–17

允许：
- 为未来工具化保留稳定边界
- Trace 字段逐步标准化
- ADR 设计冻结

禁止：
- Agent Runtime 实现
- Tool Registry 实现
- Repo Explorer 实现
- Agent Event Log 大规模建设
- Coding Agent

如果 Hybrid、Reranker、拒答、Evidence Verifier 或 P2 Evaluation 未完成验收，Harness 工作立即停止并优先修复 P2。

### P3 — Day 18–28

在 Code RAG 主线中逐步落地：
- Tool Registry
- Repo Explorer
- read-only repository tools
- Evidence Pack
- Agent-level Trace foundation

仍不开放代码写入或 shell。

### P4 — Day 31–37

Agent 实现围绕 Thin Agent + 成熟 Harness，而不是增加职责重叠的 Agent 节点。

## Rejected Alternatives

### LangGraph-centric architecture

拒绝把 LangGraph nodes 当作业务架构。核心能力必须可独立测试和复用。

### Multi-Agent-first architecture

当前不优先拆 Planner / Searcher / Critic。只有出现真正独立的 Context、capability 和 evaluation target 时才考虑独立 Agent。

### Build Coding Agent during P2

明确拒绝。edit / shell / worktree / automatic repository modification 统一进入 v2 backlog。

### Build full Harness before business tools

拒绝先建设 plugin marketplace、generic runtime framework、message bus、event sourcing 或复杂 context compression。Harness 必须随 P3–P6 的真实业务能力逐步落地。

## Guardrails

出现以下情况时回退：
- Harness 开发时间开始超过当前阶段核心业务
- Tool 抽象需要大量泛型但没有真实工具收益
- Repo Explorer 演化成职责重叠的多 Agent
- Trace 需要独立消息总线才能运行
- Context 策略没有 Token / 延迟 / 质量指标
- P2 未通过但开始开发 Agent Runtime
- v1 出现 edit / shell / worktree

原则：

```text
先交付可评测业务能力，
再把已经成立的能力纳入 Harness。
```

## Follow-up

Day 14：
- 冻结 ToolContract / ToolResult 字段
- 不重构现有 Retriever

Day 15：
- Evidence Verifier 输入输出设计为未来可复用 Tool Schema

Day 16：
- Evaluation Trace 统一加入 trace_id / git_sha / config_version

Day 17：
- P2 Gate
- Gate PASS / CONDITIONAL PASS 后建立 P3 Harness Backlog

Day 18+：
- 在 Code RAG 主线中实现 Tool Registry + Repo Explorer

## Summary

TechPilot 不演化为：

```text
LangGraph + 越来越多 Agent 节点
```

而演化为：

```text
Thin Agent Control Layer
        ↓
Thick Harness
  ├─ Tool Runtime
  ├─ Context
  ├─ Evidence / Verification
  ├─ Trace / Evaluation
  └─ Permission Boundary
        ↓
RAG / Code RAG / JD Evidence capabilities
```

v1 坚持只读分析；v2 才进入 Action / Modify / execution-based Verification。
