# TechPilot PROJECT_STATUS

## 当前版本

v0.3-dev

## 当前阶段

P0：第一条数据链路完成

## 阶段状态

- Day 1：已完成
- Day 2：已完成
- Day 3：已完成
- Day 4–5：待开始

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
- 实现 Markdown Parser
- 实现 PDF Parser
- 保留 Markdown 标题路径和行号
- 保留 PDF 页码范围
- 实现结构优先 Chunker
- 生成稳定 `chunk_id`
- 保存 Chunk JSONB Metadata
- 扩展 Document 摄取元数据
- 建立四态状态机：
  - `PENDING`
  - `COMPLETED`
  - `PARTIAL`
  - `FAILED`
- 解析失败时保留 FAILED Document
- Chunk 与最终状态在同一事务中提交
- 完成 API、Service、Parser、Chunker 测试
- 完成真实 Markdown/PDF E2E
- 通过 Swagger 上传 5 份真实技术文档
- 根据真实数据修正 heading-only 过度切块问题

## Day 3 验收证据

- `pytest -q`：17 passed
- `alembic current`：`eb1c65724726 (head)`
- Markdown E2E：PASS
- PDF E2E：PASS
- Swagger 网页上传：PASS
- 5 份真实文档全部为 `COMPLETED`
- 0 份文档没有 Chunk
- 最终生成 179 个 Chunk
- heading-only Chunk：0
- 小于 50 字符的 Chunk：0
- 最大 Chunk 长度不超过 1200 字符

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
ParsedDocument
  ↓
StructureAwareChunker
  ↓
Document + Chunk ORM
  ↓
PostgreSQL
```

## 已知非阻塞问题

- FastAPI TestClient 触发 Starlette/httpx 弃用警告。
- 上传文件当前会整体读入内存，尚未实现文件大小限制和流式处理。
- 当前仅支持 Markdown 与文本型 PDF。
- 扫描型 PDF 尚未支持 OCR。
- 重复上传目前允许生成新的 Document 记录。
- 当前使用字符数限制，不是真实 tokenizer token 数。

## 下一步

Day 4–5：基础检索

- 接入一个 Embedding 模型
- 接入 Qdrant
- 实现 Dense Retrieval
- 不使用 LangChain 一键封装核心检索链路
- 建立至少 30 条最小检索评测集
- 计算 Recall@5 和 MRR
- 保存可复现的 Retrieval Baseline
