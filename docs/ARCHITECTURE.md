# TechPilot ARCHITECTURE

> 本文只描述系统如何工作。项目进度见 `PROJECT_STATUS.md`，历史决策见 `DEV_LOG.md`，操作步骤见 `RUNBOOK.md`。

## 1. 核心边界

- PostgreSQL 是事实来源，保存 Workspace、Document、Chunk 及完整正文。
- Qdrant 是可重建的 Dense 向量索引，不保存完整 Chunk 正文。
- Retrieval 负责召回候选证据，不负责判断 Evidence Sufficiency。
- Citation 只能绑定真实进入 Context 的 Chunk。
- 无充分证据时返回 Refused。
- 所有检索必须先限定合法候选集：当前 `workspace_id`、未软删除文档；BM25 还只接受 COMPLETED/PARTIAL Document。
- Dense、BM25 与 Hybrid 共享同一 Chunk 身份空间。
- Hybrid 只融合 Retriever 已经召回的候选，不能恢复 Dense/BM25 都没有召回的 Chunk。

## 2. 文档摄取链路

```text
HTTP Upload
  ↓
FastAPI documents route
  ↓
IngestionService
  ↓
ParserRouter
  ↓
MarkdownParser / PDFParser
  ↓
StructureAwareChunker
  ↓
Document + Chunk ORM
  ↓
PostgreSQL Commit
  ↓
IndexingService
  ├── EmbeddingProvider
  └── VectorRepository
        ↓
      Qdrant
```

关键约束：

- Document / Chunk 先提交 PostgreSQL，再执行 Dense 向量索引。
- Qdrant Point ID 使用 PostgreSQL Chunk 主键。
- Qdrant Payload 只保存检索与引用所需元数据。
- Chunk 业务 ID 基于结构与正文生成稳定 SHA-256；corpus 内容或切块变化时 Golden 标签必须重新核对。

## 3. 检索链路

### 3.1 Dense Retrieval

```text
User Query
  ↓
DenseRetrievalService
  ↓
EmbeddingProvider(query: ...)
  ↓
VectorRepository
  ↓
Qdrant Top-K + workspace filter
  ↓
VectorSearchHit(point_id, score, payload)
```

当前模型：

- `intfloat/multilingual-e5-base`
- Query：`query:` 前缀
- Document：`passage:` 前缀
- 768 维归一化向量
- Cosine Distance

### 3.2 BM25 Retrieval

```text
User Query
  ↓
BM25RetrievalService
  ├── mixed Chinese / technical tokenizer
  └── BM25ChunkRepository
        ↓
      PostgreSQL legal Chunk corpus
        ↓
      BM25 score / sort / Top-K
```

BM25 合法语料边界：

- `Document.workspace_id == workspace_id`
- `Document.deleted_at IS NULL`
- `Document.status IN (COMPLETED, PARTIAL)`

Tokenizer：

- ASCII 技术 token 先整体识别并 lowercase，例如 `workspace_id`、`Recall@5`、`SHA-256`、`3.11`
- 连续中文片段交给 jieba
- 标点与空白不作为 token

当前 BM25 baseline 只对 `chunk.text` 评分，避免把 Day 11 诊断脚本中的 metadata-enriched lexical design 偷渡为生产基线。

### 3.3 RRF Hybrid Retrieval

```text
                    ┌─ DenseRetrievalService ── Top candidate_limit
User Query ─────────┤
                    └─ BM25RetrievalService ─── Top candidate_limit
                                   ↓
                          reciprocal_rank_fusion
                                   ↓
                              Hybrid Top limit
```

RRF：

```text
RRF(d) = Σ 1 / (k + rank_i(d))
```

关键边界：

- 不直接相加 Dense 与 BM25 原始 score；两者 score scale 和语义不同。
- RRF 只依赖各 Retriever 内部 rank。
- 跨 Retriever 使用稳定 `chunk_id` 去重和聚合。
- 同一路重复 Chunk 不允许重复贡献。
- `candidate_limit` 是每一路进入 fusion 的候选深度。
- `limit` 是 Fusion 后最终返回的 Top-K。
- `HybridSearchHit` 保留 `dense_rank`、`bm25_rank`、两路原始 score 和 `rrf_score`，原始 score 只用于诊断，不参与 RRF。
- 同一个 `chunk_id` 若 Dense/BM25 返回的物理/业务身份不一致，Hybrid 直接报 integrity error。

当前正式 baseline：

```text
candidate_limit = 20
rrf_k = 60
final top_k = 5
```

### 3.4 当前生产边界

```text
AnswerService
  ↓
DenseRetrievalService        ← 当前生产路径

BM25RetrievalService         ← 独立实现 / evaluation path
HybridRetrievalService       ← 独立实现 / evaluation path
```

Day 13 没有为了 Hybrid 重构 `AnswerService`。Reranker 完成并形成 P2 证据前，不提前改变生产回答链路。

## 4. 可信问答链路

```text
POST /answers
  ↓
AnswerService
  ├── Workspace 校验
  ├── DenseRetrievalService
  ├── PostgreSQL Chunk 正文回查
  ├── Context Enricher
  ├── Context Builder
  └── DeepSeek Provider
        ↓
      Answer + Citation + Refused
```

关键约束：

- 完整正文必须从 PostgreSQL 回查。
- 已删除 Document 不得进入正文回查。
- LLM 只可使用内部 `SOURCE_N` 选择 Citation；`SOURCE_N` 不得泄漏到用户可见正文。
- Retrieval Relevance 不等于 Evidence Sufficiency。
- Entity Scope Mismatch 或关键关系证据不足时必须拒答。
- Refused 不返回 Citation。

## 5. 删除链路

```text
DELETE /documents/{document_id}
  ↓
DocumentService
  ↓
PostgreSQL Soft Delete
  ↓
Commit
  ↓
Best-effort Qdrant Cleanup
```

关键约束：

- 先提交 PostgreSQL 删除状态，再清理 Qdrant。
- Qdrant Cleanup 失败不回滚事实来源。
- 长期可靠清理方案仍为 Outbox Pattern。

## 6. 评测链路

### Retrieval

```text
Current Legal Corpus Snapshot
        +
30-case Golden Dataset
        ↓
Strict Golden integrity check
        ↓
Dense Top-N + BM25 Top-N
        ↓
Single shared candidate snapshot
        ├── Dense Top-5 metrics
        ├── BM25 Top-5 metrics
        └── RRF Fusion → Hybrid Top-5 metrics
        ↓
Recall@5 / MRR@5 / Failure-set comparison
```

正式 Day 13 结果：

- Dense：Recall@5 0.700000 / MRR@5 0.500000 / MISS 9
- BM25：Recall@5 0.700000 / MRR@5 0.567778 / MISS 9
- Hybrid：Recall@5 0.766667 / MRR@5 0.588333 / MISS 7
- Dense-only hit：4
- BM25-only hit：4
- Both hit：17
- Both miss：5
- Dense/BM25 Top-5 hit union：25/30
- Hybrid actual hit：23/30
- Fusion loss：2

Golden integrity 不只校验 `expected_chunk_id` 是否存在，还必须校验其与当前 workspace、active/legal Document、`expected_document_id`、名称、chunk index 和 section 的一致性。

评测运行记录必须绑定 dataset hash、corpus snapshot hash、git SHA 和检索配置。开发阶段若工作树尚未 commit，git SHA 只代表当前基线 commit，不能误写成包含未提交实验代码的实现 SHA。

### Answer

```text
Answer Cases
  ↓
answer_eval.py
  ↓
真实 AnswerService
  ↓
Answer correctness / Citation support / Refused / Runtime errors
```

Day 11 answerable 生产评测：4/7 answer correct、4/7 citation supported、1/7 over-refusal，因此 P1 Gate = FIX。

## 7. Agent / Harness 边界

详细决策见 `docs/adr/ADR-001-agent-runtime.md`。

冻结原则：

```text
Thin Agent Control Layer
        ↓
Thick Harness
  ├── Tool Runtime
  ├── Context
  ├── Evidence / Verification
  ├── Trace / Evaluation
  └── Permission Boundary
```

Day 13–17 仍以 P2 为最高优先级，只允许 Harness 设计冻结，不实现 Agent Runtime。

v1 只读：

```text
Research / Understand / Analyze / Plan
```

`edit_file`、shell、worktree、自动代码修改等 Action 能力属于 v2 backlog。

## 8. 当前未实现能力

- Reranker
- Evidence Verifier
- 持久化 / 缓存化 BM25 lexical index
- OCR
- Outbox Pattern
- 流式上传和文件大小限制
- Code RAG
- Tool Registry / Agent Runtime
