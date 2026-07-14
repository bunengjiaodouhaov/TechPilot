# TechPilot Project Context

## 项目定位

TechPilot 是一个面向开发者的技术调研、文档检索与代码理解平台。

## 仓库

- Repository: `https://github.com/bunengjiaodouhaov/TechPilot`
- Branch: `main`
- 当前本地包含尚待提交的 Day 4–5 改动

## 当前进度

- Day 1：完成
- Day 2：完成
- Day 3：完成
- Day 4–5：完成并通过验收
- 下一阶段：Day 6–7 可信问答

## 当前技术栈

- Python 3.11
- FastAPI
- SQLAlchemy Async
- Alembic
- PostgreSQL 16
- Redis
- Qdrant
- pypdf
- sentence-transformers
- pytest / pytest-asyncio

## 当前核心能力

### 文档摄取

- `POST /documents/upload`
- Markdown / PDF Parser
- StructureAwareChunker
- Document 四态状态机
- 稳定 `chunk_id`
- PostgreSQL Document / Chunk 持久化

### 基础检索

- `intfloat/multilingual-e5-base`
- 768 维归一化 Embedding
- E5 `passage:` / `query:` 前缀
- Qdrant Cosine Collection
- Workspace Filter
- IndexingService
- DenseRetrievalService
- 30 条 Golden Dataset
- Recall@5 / MRR Baseline

## 当前调用链

```text
Upload
  ↓
IngestionService
  ↓
Parser + Chunker
  ↓
PostgreSQL Commit
  ↓
IndexingService
  ├── EmbeddingProvider
  └── QdrantRepository
        ↓
      Qdrant

Query
  ↓
DenseRetrievalService
  ├── EmbeddingProvider
  └── QdrantRepository
        ↓
      VectorSearchHit
```

## 关键冻结设计

- PostgreSQL 是事实来源，Qdrant 是可重建索引。
- Point ID 使用 PostgreSQL Chunk 主键。
- 一个环境使用一个 Collection。
- 所有检索必须带 `workspace_id` Filter。
- Collection 使用 Cosine Distance，向量维度为 768。
- Payload 保存引用所需元数据，不保存完整 Chunk 正文。
- Indexing 在 PostgreSQL Commit 后执行。
- 核心检索链路不使用 LangChain 一键封装。
- `eval/` 只保留本地，不提交 GitHub。

## 验收结果

- pytest：47 passed
- Qdrant Points：179
- Golden Dataset：30
- Recall@5：0.866667
- MRR@5：0.627778
- MISS：4
- `scripts/retrieval_eval.py` 编译通过

## 已知非阻塞问题

- TestClient 的 Starlette/httpx 弃用警告
- 文件整体读入内存
- 无上传大小限制
- 仅支持 Markdown 和文本型 PDF
- 扫描 PDF 无 OCR
- 暂无重复文件去重
- 字符数不是 tokenizer token 数

## 下一阶段

Day 6–7：可信问答

- 实现 Context Builder
- 接入 LLM 回答
- 每条回答必须返回文档名、页码和原文片段
- 加入 10 条无答案问题并记录错误回答率

## 协作规则

- 总控手册决定当前阶段和验收要求。
- 修改代码前先说明目标、文件、数据链路和验证方式。
- 代码完成不等于 Day 完成。
- 每日收尾必须更新状态、开发日志、必要 Runbook、面试笔记和本地 Review。
- 新对话应先读取 `PROJECT_CONTEXT.md`。
