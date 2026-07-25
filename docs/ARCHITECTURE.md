# TechPilot ARCHITECTURE

> 本文只描述系统如何工作。项目进度见 `PROJECT_STATUS.md`，历史决策见 `DEV_LOG.md`，操作步骤见 `RUNBOOK.md`。

## 1. 核心边界

- PostgreSQL 是事实来源，保存 Workspace、Document、Chunk 及完整正文。
- Qdrant 是可重建的向量检索索引，不保存完整 Chunk 正文。
- Retrieval 负责召回相关证据，不负责决定证据是否足以回答。
- Citation 必须由系统根据真实进入 Context 的 Chunk 构造。
- 无充分证据时返回 Refused。
- 所有检索强制按 `workspace_id` 隔离。

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

- Document 先进入 PostgreSQL，再执行向量索引。
- PostgreSQL Commit 成功后才允许写入 Qdrant。
- Qdrant Point ID 使用 PostgreSQL Chunk 主键。
- Payload 只保存检索与引用所需元数据。

## 3. 检索链路

```text
User Query
  ↓
DenseRetrievalService
  ├── EmbeddingProvider
  └── VectorRepository
        ↓
      Qdrant Top-K
  ↓
按 workspace_id 过滤
  ↓
返回 VectorSearchHit
```

当前检索模型：

- `intfloat/multilingual-e5-base`
- Query 使用 `query:` 前缀
- Document 使用 `passage:` 前缀
- 768 维
- Cosine Distance

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

- 回答上下文中的完整正文必须从 PostgreSQL 回查。
- 已删除 Document 不得进入正文回查结果。
- 模型回答前必须检查主体、属性及其关系是否由证据支持。
- Retrieval Relevance 不等于 Evidence Sufficiency。
- Entity Scope Mismatch 时必须拒答。
- Refused 回答不返回 Citation。

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

- 删除先提交 PostgreSQL，再清理 Qdrant。
- Qdrant 清理失败不回滚数据库软删除。
- Dense Retrieval 与 Chunk 回查均排除已删除文档。
- 当前一致性策略为 Best-effort Cleanup。
- 长期方案为 Outbox Pattern。

## 6. 当前评测链路

```text
Golden Dataset
  ↓
retrieval_eval.py
  ↓
Recall@5 / MRR@5 / Failure JSONL

Answer Cases
  ↓
answer_eval.py
  ↓
真实 AnswerService
  ↓
Refused / Citation / JSONL
```

当前基线：

- Golden Dataset：30 条
- Recall@5：0.866667
- MRR@5：0.627778
- Answer Evaluation：3/3 PASS
- 自动化测试：119 passed

## 7. 当前未实现能力

- OCR
- BM25
- Hybrid Retrieval
- Reranker
- Outbox Pattern
- 流式上传和文件大小限制
- Code RAG
