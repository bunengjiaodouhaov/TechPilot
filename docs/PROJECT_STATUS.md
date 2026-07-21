# TechPilot PROJECT_STATUS

## 当前版本

v0.5-dev

## 当前阶段

P1：文档 RAG — 可信问答主链路完成

## 阶段状态

- Day 1：已完成
- Day 2：已完成
- Day 3：已完成
- Day 4–5：已完成
- Day 6：已完成
- Day 7：待开始

## 已完成

### Day 1：冻结项目

- 创建 GitHub 仓库并完成首次提交
- 提交产品范围基线
- 创建 P0–P7 GitHub Milestones
- 建立 FastAPI 最小工程
- 实现 `GET /health`
- 完成基础自动化测试

### Day 2：基础设施

- 使用 Docker Compose 启动 PostgreSQL、Redis、Qdrant
- 使用 Pydantic Settings 管理环境配置
- 建立 SQLAlchemy 异步数据库连接
- 初始化 Alembic
- 建立 Workspace、Document、Chunk 模型
- 实现 `GET /health/dependencies`
- 完成依赖健康检查

### Day 3：第一条数据链路

- 实现 `POST /documents/upload`
- 实现 Markdown Parser 与 PDF Parser
- 实现结构优先 Chunker
- 生成稳定 `chunk_id`
- 保存 Chunk JSONB Metadata
- 建立 Document 四态状态机
- 完成上传、解析、入库 E2E
- 通过 Swagger 上传 5 份真实技术文档
- 最终生成 179 个有效 Chunk

### Day 4–5：基础检索

- 接入 `intfloat/multilingual-e5-base`
- 固定向量维度为 768，使用归一化 Embedding
- 实现独立 `EmbeddingProvider`
- 实现 Qdrant `VectorRepository`
- 实现 Qdrant Collection 创建、Upsert 和 Workspace 过滤检索
- 实现 `IndexingService`
- 将文档摄取链路接入自动向量索引
- 实现 `DenseRetrievalService`
- 不使用 LangChain 一键封装核心检索链路
- 建立 30 条人工标注 Golden Dataset
- 实现可复现的 Dense Retrieval 评测脚本
- 计算并保存 Recall@5 与 MRR Baseline
- 失败案例自动写入本地 JSONL

### Day 6：可信问答

- 新增 Answer 与 Citation 数据契约
- 实现 Context Builder
- 实现 Context Enricher
- 实现 DeepSeek Provider
- 实现 AnswerService
- 将 Dense Retrieval 接入回答链路
- 根据检索结果回查 PostgreSQL Chunk 正文
- 实现 `POST /answers`
- 返回结构化 Citation
- 支持无证据拒答
- 完成真实 Answer E2E

## Day 4–5 验收证据

- `python -m py_compile scripts/retrieval_eval.py`：PASS
- `pytest -q`：47 passed
- Qdrant Repository Smoke：PASS
- 真实文档向量索引：179 Points
- Golden Dataset：30 条
- Recall@5：0.866667
- MRR@5：0.627778
- 失败案例：4 条
- `eval/`：仅本地，不提交 GitHub

## Day 6 验收证据

- `POST /answers`：HTTP 200
- Workspace 校验：PASS
- Dense Retrieval：PASS
- PostgreSQL Chunk 正文回查：PASS
- Context Builder：PASS
- DeepSeek API：PASS
- Citation：PASS
- Refused：PASS
- Real End-to-End：PASS

## 当前架构

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
PostgreSQL
  ↓
IndexingService
  ├── EmbeddingProvider
  └── VectorRepository
        ↓
      Qdrant

User Query
  ↓
POST /answers
  ↓
AnswerService
  ├── Workspace 校验
  ├── DenseRetrievalService
  │     ├── EmbeddingProvider
  │     └── VectorRepository
  │           ↓
  │         Top-K VectorSearchHit
  ├── PostgreSQL Chunk 正文回查
  ├── Context Enricher
  ├── Context Builder
  └── DeepSeek Provider
        ↓
      Answer + Citation + Refused
```

## 已知非阻塞问题

- FastAPI TestClient 触发 Starlette/httpx 弃用警告。
- 上传文件当前会整体读入内存，尚未实现文件大小限制和流式处理。
- 当前仅支持 Markdown 与文本型 PDF。
- 扫描型 PDF 尚未支持 OCR。
- 重复上传目前允许生成新的 Document 记录。
- 当前使用字符数限制，不是真实 tokenizer token 数。
- Dense Retrieval 的 4 条失败案例保留在本地，后续阶段再分析，不阻塞当前验收。
- 首次回答会触发 Embedding 模型冷启动，请求耗时明显高于后续请求。
- 当前 Context Builder 采用 Top-K 上下文组织方式，尚未加入 Reranker。
- 当前尚未完成系统化回答质量、引用正确性和拒答评测。
- 部分早期知识库文档可能已经过时。

## 下一步

Day 7：回答质量评测

- 建立 Answer Evaluation Dataset
- 加入至少 10 条无答案问题
- 统计 Refused Accuracy
- 检查 Citation 是否支持回答
- 记录错误回答率
- 优化 Prompt
- 优化 Context Builder
- 为后续 Hybrid Retrieval 做准备
