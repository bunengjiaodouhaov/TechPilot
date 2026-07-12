# TechPilot Project Context

## 项目定位

TechPilot 是一个面向开发者的技术调研、文档检索与代码理解平台。

## 仓库

- Repository: `https://github.com/bunengjiaodouhaov/TechPilot`
- Branch: `main`
- Day 2 base commit: `230adf3`
- 当前本地包含尚待提交的 Day 3 改动

## 当前进度

- Day 1：完成
- Day 2：完成
- Day 3：完成并通过验收
- 下一阶段：Day 4–5 基础检索

## 当前技术栈

- Python 3.11
- FastAPI
- SQLAlchemy Async
- Alembic
- PostgreSQL 16
- Redis
- Qdrant
- pypdf
- pytest / pytest-asyncio

## Day 3 已实现

- `POST /documents/upload`
- Markdown Parser
- PDF Parser
- ParserRouter
- StructureAwareChunker
- IngestionService
- Document 四态状态机
- 稳定 `chunk_id`
- Chunk JSONB Metadata
- Markdown 标题路径与行号
- PDF 页码范围
- 失败事务与 FAILED Document
- Swagger 网页上传
- 真实 PostgreSQL E2E

## 核心调用链

```text
app/main.py
  ↓
app/api/documents.py
  ↓
app/api/dependencies.py
  ↓
app/ingestion/service.py
  ↓
app/ingestion/router.py
  ↓
app/ingestion/parsers/
  ↓
app/ingestion/chunker.py
  ↓
app/models/document.py + app/models/chunk.py
  ↓
PostgreSQL
```

## Document 状态

```text
PENDING
COMPLETED
PARTIAL
FAILED
```

不使用 `PROCESSING`，因为当前摄取流程是同步执行。未来改为异步任务时，再考虑独立 IngestionJob。

## Chunk 策略

- 结构优先
- 最大长度：1200 字符
- 同一标题下合并短段落
- 超长段落按字符长度兜底拆分
- Markdown 标题路径注入正文
- 标题不单独生成 Chunk
- PDF 允许跨页合并
- 保留 `page_start`、`page_end`
- `chunk_id` 不依赖全局 `chunk_index`

## 验收结果

- 自动化测试：17 passed
- Alembic：`eb1c65724726 (head)`
- Markdown E2E：PASS
- PDF E2E：PASS
- Swagger 上传：PASS
- 真实文档：5
- COMPLETED：5
- zero chunk documents：0
- 最终 Chunk：179
- heading-only Chunk：0
- 小于 50 字符 Chunk：0

## 已知非阻塞问题

- TestClient 的 Starlette/httpx 弃用警告
- 文件整体读入内存
- 无上传大小限制
- 仅支持 Markdown 和文本型 PDF
- 扫描 PDF 无 OCR
- 暂无重复文件去重
- 字符数不是 tokenizer token 数

## 下一阶段

Day 4–5：基础检索

- 接入 Embedding 模型
- 写入 Qdrant
- 实现 Dense Retrieval
- 不使用 LangChain 一键封装核心检索逻辑
- 建立至少 30 条检索评测集
- 计算 Recall@5 和 MRR
- 保存 Baseline

## 协作规则

- 先明确需求和约束，再冻结设计。
- 修改代码前必须说明：
  - 本次目标
  - 新增文件
  - 修改文件
  - 数据调用链
- 用户重点掌握模块职责、数据流、事务和验证方法。
- 具体框架样板代码由助手提供脚本完成。
- 每日收尾必须更新项目文档和本地 Review。
- 新对话应先读取 `PROJECT_CONTEXT.md`。
