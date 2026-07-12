# TechPilot

TechPilot 是一个面向开发者的技术调研、文档检索与代码理解平台。

当前已完成 Day 1–3：项目范围冻结、基础设施和第一条文档摄取链路。

## 当前已实现

- 产品基线文档与 P0–P7 Milestones
- FastAPI 服务与 Swagger
- `GET /health`
- `GET /health/dependencies`
- PostgreSQL、Redis、Qdrant 基础设施
- SQLAlchemy 异步数据库连接
- Alembic Schema 迁移
- Workspace、Document、Chunk 数据模型
- Markdown 与 PDF 文件上传
- Markdown 标题路径和行号保留
- PDF 页码范围保留
- 结构优先的 Chunking
- 稳定 `chunk_id`
- Chunk JSONB Metadata
- Document 状态管理：
  - `PENDING`
  - `COMPLETED`
  - `PARTIAL`
  - `FAILED`
- 上传失败记录保留与事务回滚
- Swagger、自动化测试和真实 PostgreSQL 端到端验证

## 环境要求

- Python 3.11+
- Docker Desktop
- Docker Compose

## 本地运行

### macOS / Linux

```bash
conda deactivate 2>/dev/null || true
source .venv/bin/activate

docker compose up -d
python -m pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

访问：

- 健康检查：http://127.0.0.1:8000/health
- 依赖检查：http://127.0.0.1:8000/health/dependencies
- API 文档：http://127.0.0.1:8000/docs
- 文档上传：`POST /documents/upload`

## 上传文档

上传前需要存在 Workspace。

```bash
docker compose exec postgres psql           -U techpilot           -d techpilot           -c "INSERT INTO workspace (name) VALUES ('TechPilot Default') RETURNING id, name;"
```

然后在 Swagger 中调用：

```text
POST /documents/upload
```

表单字段：

```text
workspace_id
file
```

当前支持：

```text
.md
.markdown
.pdf
```

## 运行测试

```bash
pytest -q
```

Day 3 完成时：

```text
17 passed
```

## 数据库迁移

```bash
alembic current
```

Day 3 完成时数据库 revision：

```text
eb1c65724726 (head)
```

## 项目文档

- `docs/PROJECT_STATUS.md`
- `docs/DEV_LOG.md`
- `docs/RUNBOOK.md`
- `docs/LEARNING_PROTOCOL.md`
- `docs/INTERVIEW_NOTES.md`
- `PROJECT_CONTEXT.md`
