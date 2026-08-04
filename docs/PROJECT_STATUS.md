# TechPilot PROJECT_STATUS

## 当前版本

v0.7-dev

## 当前阶段

P2：高质量 RAG — Day 13 RRF Hybrid Retrieval 已完成；P1 Gate 仍为 `FIX`

## 阶段状态

- P0 工程骨架：完成
- P1 文档 RAG：工程闭环完成，但质量 Gate = `FIX`
- Day 11：P1 answerable 质量复验完成，定位检索召回为主要瓶颈
- Day 12：BM25 实现、正式评测与 Dense/BM25 对比完成
- Day 13：RRF Hybrid Retrieval、真实评测、失败分析与 Agent Runtime ADR 完成
- P2 高质量 RAG：进行中
- 下一步：Day 14 Reranker + 延迟/收益实验

## 已完成

### P0 / P1 核心能力

- FastAPI、PostgreSQL、Redis、Qdrant、SQLAlchemy、Alembic 工程骨架
- Markdown / 文本型 PDF 摄取与结构优先 Chunking
- 稳定 Chunk ID 与 PostgreSQL 权威正文存储
- `multilingual-e5-base` Dense Retrieval 与 Workspace 隔离
- DeepSeek 可信回答、Citation 绑定、证据不足拒答
- Document 软删除、Qdrant Best-effort Cleanup 与删除后隔离
- Upload -> Persist -> Index -> Retrieve -> Answer -> Cite -> Delete -> Refuse 生命周期验证

### Day 11：P1 质量复验

- 建立 7 条 `answerable=true` 生产评测样本
- Answer correctness：4/7
- Citation support：4/7
- Over-refusal：1/7
- Runtime errors：0
- 三个失败目标 Dense rank：56 / 1120 / 26
- 生产 Prompt 增加 `SOURCE_N` 输出隔离规则
- P1 Gate 最终更新为 `FIX`
- 结论：继续改 Prompt 不能弥补权威 Chunk 未进入 Top-K，后续优先做检索优化

### Day 12：BM25 Baseline

- 新增 BM25 tokenizer、DTO、Repository、Retrieval Service
- 中英混合 tokenizer 保留技术标识符和数字，中文使用 jieba
- 配置 `bm25_k1=1.5`、`bm25_b=0.75`
- BM25 合法候选集限定为：当前 Workspace、`deleted_at IS NULL`、Document 状态 COMPLETED/PARTIAL
- BM25 与 Dense 使用同一 Chunk 身份空间
- 新增 tokenizer、评分、参数、limit、Workspace 和语料过滤测试
- 新增 `scripts/bm25_retrieval_eval.py`
- 完整自动化回归：PASS

### Day 13：RRF Hybrid Retrieval

- 新增纯 RRF fusion：只使用 rank，不混合 Dense/BM25 原始 score
- 使用统一 `chunk_id` 做跨 Retriever dedupe；同一路重复 Chunk 不重复贡献
- 新增 `HybridSearchHit`，保留 `dense_rank`、`bm25_rank`、两路原始 score 与 `rrf_score`
- 新增独立 `HybridRetrievalService`
- 明确 `candidate_limit` 与最终 `limit` 的职责边界
- 对 Dense/BM25 共享 Chunk 做 identity consistency 校验
- 新增 RRF 与 Hybrid Service 自动化测试
- 新增 `scripts/hybrid_retrieval_eval.py`
- 评测脚本先校验 Golden 与当前 legal corpus，再运行 Dense/BM25/RRF
- 真实 Hybrid Service smoke：PASS
- 完整自动化回归：154 passed
- `git diff --check`：PASS
- 生产 `AnswerService` 保持 Dense-only；Day 13 未提前接入 Hybrid
- 新增 `docs/adr/ADR-001-agent-runtime.md`，冻结 Thin Agent / Thick Harness 与 v1/v2 边界；未实现 Agent Runtime

## 验收证据

### Golden Dataset Integrity

Day 12 曾发现原 30 条 Golden 中有 6 条指向已删除旧文档，完成 corpus / label drift 修复。

Day 13 又发现一条 Golden 的 `expected_document_id` metadata 错误。Day 12 的 Retrieval HIT/MISS 实际只按 `expected_chunk_id` 判定，因此修复后 Dense/BM25 指标完全复现；但这暴露出 Day 12 的 Golden integrity check 对完整身份关系校验不足。

Day 13 已将正式前置校验收紧为：

- 当前 `workspace_id`
- 当前 legal Document：`deleted_at IS NULL`
- Document 状态 COMPLETED/PARTIAL
- `expected_document_id`
- `expected_document_name`
- `expected_chunk_id`
- `expected_chunk_index`
- `expected_section`

修复后：

- Golden：30/30 valid
- legal documents：5
- legal chunks：1153
- dataset SHA-256：`e65e8490ef8e23018673712b2e595c6779e842094bd49d5d433fc5641bcef7f5`
- corpus snapshot SHA-256：`1d393523789b235bcfc1f821491bf86c5bcd29f47e04da7ebb85362b9ad81b0e`

规则：任何 Retrieval Benchmark 前必须先检查 Golden 与当前 legal corpus snapshot 的一致性；不得用 Retriever 输出自动重标 stale case。

### Dense / BM25 / Hybrid（当前 corpus / 当前 Golden）

正式 Day 13 baseline：

| Retriever | Recall@5 | MRR@5 | MISS |
| --- | ---: | ---: | ---: |
| Dense | 0.700000 | 0.500000 | 9 |
| BM25 | 0.700000 | 0.567778 | 9 |
| Hybrid RRF | 0.766667 | 0.588333 | 7 |

正式 Hybrid 配置：

```text
candidate_limit = 20
top_k = 5
rrf_k = 60
```

Dense/BM25 Top-5 失败集合：

- Dense-only hit：4
- BM25-only hit：4
- Both hit：17
- Both miss：5
- 单路 Top-5 命中并集：25/30，理论 oracle upper bound = 0.833333

Hybrid：

- 实际命中：23/30
- Dense-only preserved：3/4
- BM25-only preserved：3/4
- Fusion losses：2
- Both candidate miss（Top-20）：3
- Hybrid rescues from both Top-5 miss：0

失败分析：

- case 11：Dense rank 2、BM25 candidate miss；较大 `k` 下被多条双路中等排名候选压出 Hybrid Top-5
- case 09：Dense rank 3、BM25 rank 14；较小 `k` 会过度强化单路头部，反而损失该跨路共识命中
- case 15：BM25-only rank 5，在双路候选合并后的 final Top-5 中天然脆弱
- case 25：BM25 candidate rank 17、Dense Top-20 miss，属于候选召回/排序问题，不是 RRF 能自动修复的问题

有边界参数实验：

| candidate_limit | rrf_k | Hybrid Recall@5 | Hybrid MRR@5 | 结论 |
| ---: | ---: | ---: | ---: | --- |
| 20 | 60 | 0.766667 | 0.588333 | 正式 baseline |
| 10 | 60 | 0.766667 | 0.595556 | 不增 Recall，candidate both-miss 由 3 增至 4 |
| 20 | 20 | 0.766667 | 0.588333 | 与 k=60 Top-5 结果相同 |
| 20 | 1 | 0.766667 | 0.586111 | 不增 Recall，fusion loss 从 case 11 转移到 case 09 |

结论：RRF 确认利用了 Dense/BM25 的互补性，但不能保证达到单路成功集合的 oracle union；继续针对 30 条 Golden 搜索 `k` 会造成过拟合，因此停止调参。

### 测试

- RRF focused tests：PASS
- Hybrid Service tests：PASS
- Hybrid real-service smoke：PASS
- Full Test Suite：154 passed
- `git diff --check`：PASS
- 非阻塞警告：Starlette TestClient / httpx deprecation warning
- HF Hub 未认证警告不影响当前 smoke 结果

## 架构文档

系统架构、数据流和 Dense/BM25/Hybrid 当前边界统一维护在 `docs/ARCHITECTURE.md`。

Agent 工程边界维护在 `docs/adr/ADR-001-agent-runtime.md`；Day 13 只做设计冻结，不启动 Agent Runtime。

## 已知非阻塞问题

- 生产 `AnswerService` 仍使用 Dense Top-K；Hybrid 当前是独立 Retriever / evaluation path
- Reranker 尚未实现
- Hybrid 仍有 7/30 MISS，其中 3 条在 Dense/BM25 Top-20 候选中都未出现
- RRF final Top-K 会产生 fusion truncation loss，不能等同于 Dense/BM25 oracle union
- BM25 当前按合法 PostgreSQL Chunk 集构建请求级 baseline，尚未做持久化 lexical index / cache
- Context Budget 使用字符数，不是真实 tokenizer token 数
- 仅支持文本型 PDF，不支持 OCR
- Qdrant 删除采用 Best-effort Cleanup
- Starlette/httpx TestClient 弃用警告仍存在
- PR #2 仍为 Draft / Open / 未合并

## 下一步

Day 14 主线：Reranker 接入与延迟/收益实验。必须继续使用当前有效 Golden 与可复现实验配置；不把 Day 11 的诊断实验直接当成 Day 14 正式证据。

按 Agent Harness 补充说明，Day 14 只额外花 20–30 分钟在 ADR 中冻结 `ToolContract` / `ToolResult` 字段；不实现 Tool Registry，不重构现有 Retriever，不启动 Coding Agent。
