# TechPilot PROJECT_STATUS

## 当前版本

v0.7-dev

## 当前阶段

P2：高质量 RAG — Day 12 BM25 已完成；P1 Gate 仍为 `FIX`

## 阶段状态

- P0 工程骨架：完成
- P1 文档 RAG：工程闭环完成，但质量 Gate = `FIX`
- Day 11：P1 answerable 质量复验完成，定位检索召回为主要瓶颈
- Day 12：BM25 实现、正式评测与 Dense/BM25 对比完成
- P2 高质量 RAG：进行中
- 下一步：Day 13 RRF Hybrid Retrieval

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

## 验收证据

### Golden Dataset Integrity

Day 12 发现原 30 条 Golden 中有 6 条指向已删除的旧文档 `05_techpilot_ingestion_handbook.md`。

处理：

- 确认不是 Retriever 故障，而是 corpus / label drift
- 不让 Dense/BM25 自动给自己重标答案
- 从当前盘古 PDF 中人工选择 6 个新的权威 Chunk 替换旧题
- 修复后：30/30 Golden 均指向当前 Workspace 的合法活跃 Chunk

规则：以后 Retrieval Benchmark 前必须先检查 Golden 与 corpus snapshot 一致性。

### Dense vs BM25（当前 corpus / 当前 Golden）

| Retriever | Recall@5 | MRR@5 | MISS |
| --- | ---: | ---: | ---: |
| Dense | 0.700000 | 0.500000 | 9 |
| BM25 | 0.700000 | 0.567778 | 9 |

失败集合：

- BM25 独占命中：4
- Dense 独占命中：4
- 两者共同 MISS：5
- 两路 Top-5 命中并集：25/30
- 理论覆盖上界：0.833333

结论：BM25 不替代 Dense；两路总指标相近但失败集合明显互补，为 Day 13 RRF Hybrid 提供直接实验依据。

### 测试

- BM25 tokenizer / retrieval focused tests：PASS
- BM25 PostgreSQL 过滤集成测试：PASS
- P1 lifecycle + health regression：PASS
- Full Test Suite：PASS
- 非阻塞警告：Starlette TestClient / httpx deprecation warning

## 架构文档

系统架构、数据流和 Dense/BM25 当前边界统一维护在 `docs/ARCHITECTURE.md`。

## 已知非阻塞问题

- 生产 `AnswerService` 仍使用 Dense Top-K；BM25 目前是独立 Retriever / baseline
- Hybrid Retrieval / RRF 尚未实现
- Reranker 尚未实现
- Dense 与 BM25 共同 MISS 5 条，简单 fusion 不保证全部解决
- BM25 当前按合法 PostgreSQL Chunk 集构建请求级 baseline，尚未做持久化 lexical index / cache
- Context Budget 使用字符数，不是真实 tokenizer token 数
- 仅支持文本型 PDF，不支持 OCR
- Qdrant 删除采用 Best-effort Cleanup
- Starlette/httpx TestClient 弃用警告仍存在
- PR #2 仍为 Draft / Open / 未合并

## 下一步

Day 13 实现 RRF Hybrid Retrieval：保留两路 provenance 与原始 rank，使用当前 30 条有效 Golden 对比 Dense / BM25 / Hybrid，禁止提前实现 Reranker。
