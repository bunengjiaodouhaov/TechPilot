# TechPilot ARCHITECTURE

> 本文只描述系统如何工作。项目进度见 `PROJECT_STATUS.md`，历史决策见 `DEV_LOG.md`，操作步骤见 `RUNBOOK.md`。

## 1. 核心边界

- PostgreSQL 是事实来源，保存 Workspace、Document、Chunk 及完整正文。
- Qdrant 是可重建的 Dense 向量索引，不保存完整 Chunk 正文。
- Retrieval 负责召回候选证据，不负责判断 Evidence Sufficiency。
- Citation 只能绑定真实进入 Context 的 Chunk。
- 无充分证据时返回 Refused。
- 所有检索必须先限定合法候选集：当前 `workspace_id`、未软删除文档；BM25 还只接受 COMPLETED/PARTIAL Document。
- Dense 与 BM25 必须共享同一 Chunk 身份空间，便于后续 dedupe / fusion /正文回查。

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

### 3.3 当前生产边界

```text
AnswerService
  ↓
DenseRetrievalService   ← 当前生产路径

BM25RetrievalService    ← 独立实现 / 评测路径，尚未接 AnswerService
```

Day 13 才引入 RRF Hybrid；Day 12 不做融合。

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
Current Corpus Snapshot
  +
30-case Golden Dataset
  ↓
Golden integrity check
  ↓
Dense retrieval_eval.py
BM25 bm25_retrieval_eval.py
  ↓
Recall@5 / MRR@5 / Failure JSONL
  ↓
Failure-set comparison
```

Day 12 当前正式结果：

- Dense：Recall@5 0.700000 / MRR@5 0.500000 / MISS 9
- BM25：Recall@5 0.700000 / MRR@5 0.567778 / MISS 9
- Dense-only hit：4
- BM25-only hit：4
- Both miss：5
- Top-5 hit union：25/30

Golden 必须绑定当前 corpus snapshot。文档删除、替换或重新切块后，如果目标 `chunk_id` 不再属于合法活跃语料，该样本必须重新人工标注或替换，不能把 stale label 当 Retriever MISS。

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

## 7. 当前未实现能力

- RRF Hybrid Retrieval
- Reranker
- 持久化 / 缓存化 BM25 lexical index
- OCR
- Outbox Pattern
- 流式上传和文件大小限制
- Code RAG
